from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, text
from mitraa.db.models import Responsibility, ResponsibilityEvent, Reminder, Conversation
from mitraa.db.session import make_engine, make_session_factory
from mitraa.responsibilities import service as s
from mitraa.responsibilities.schemas import ResponsibilityCreate, ResponsibilityPatch, ReminderCreate, Preferences

NOW=datetime(2026,10,20,10,tzinfo=timezone.utc)


def capture(session,**kwargs):
    return s.create(session,ResponsibilityCreate(title='Prepare application',outcome='Submit a reviewed application',
        next_action='Check eligibility',**kwargs))


def follow(session,rid,at=None):
    return s.add_reminder(session,ReminderCreate(responsibility_id=rid,message='Review eligibility',
        due_at=at or NOW+timedelta(hours=1)),now=NOW)


def test_additive_schema_preserves_old_data_and_new_items_survive_restart(tmp_path):
    url='sqlite:///'+str(tmp_path/'upgrade.db')
    factory=make_session_factory(make_engine(url))
    with factory() as session:
        session.add(Conversation(title='Existing conversation')); item=capture(session,deadline=NOW,timezone='Asia/Kolkata',
            sources=['listing.txt','listing.txt']); session.commit()
    factory=make_session_factory(make_engine(url))
    with factory() as session:
        assert session.scalar(select(Conversation)).title=='Existing conversation'
        saved=s.as_dict(s.get(session,item['id']))
        assert saved['deadline']==NOW.isoformat() and saved['sources']==['listing.txt']
        assert saved['next_action']=='Check eligibility' and len(s.history(session,item['id']))==1


def test_versions_prevent_silent_lost_updates(session,session_factory):
    item=capture(session); session.commit()
    with session_factory() as other:
        s.patch(other,item['id'],ResponsibilityPatch(expected_version=1,next_action='Prepare resume')); other.commit()
    with pytest.raises(s.Conflict):
        s.patch(session,item['id'],ResponsibilityPatch(expected_version=1,next_action='Overwrite newer work'))
    session.rollback(); session.expire_all()
    assert s.get(session,item['id']).next_action=='Prepare resume'


def test_completion_requires_confirmation_and_cancels_reminders(session):
    item=capture(session); r=follow(session,item['id']); session.commit()
    with pytest.raises(ValueError,match='confirmed'):
        s.patch(session,item['id'],ResponsibilityPatch(expected_version=1,status='completed'))
    session.rollback()
    done=s.patch(session,item['id'],ResponsibilityPatch(expected_version=1,status='completed',
        completion_note='User confirmed receipt ABC123')); session.commit()
    assert done['completed_at'] and done['completion_note'].endswith('ABC123')
    assert s.reminder(session,r['id']).status=='cancelled'
    assert s.history(session,item['id'])[-1]['details']['completion_note'].endswith('ABC123')
    reopened=s.patch(session,item['id'],ResponsibilityPatch(expected_version=2,status='active'))
    assert reopened['completed_at'] is None and reopened['completion_note']==''
    assert s.reminder(session,r['id']).status=='cancelled'


@pytest.mark.parametrize('changes',[{'title':None},{'status':None},{'timezone':'Invalid/Zone'},
    {'sources':['']},{'title':'   '},{'priority':'urgent'},{'deadline':'2026-10-22T10:00:00'}])
def test_invalid_edits_do_not_mutate_state(session,changes):
    item=capture(session); session.commit()
    with pytest.raises(ValueError):
        s.patch(session,item['id'],ResponsibilityPatch.model_validate({'expected_version':1,**changes}))
    session.rollback(); session.expire_all()
    assert s.get(session,item['id']).version==1


def test_forget_removes_children_with_foreign_keys_enabled(session):
    session.execute(text('PRAGMA foreign_keys=ON'))
    item=capture(session); follow(session,item['id']); session.commit()
    s.forget(session,item['id'],1); session.commit()
    assert list(session.scalars(select(Responsibility)))==[]
    assert list(session.scalars(select(Reminder)))==[]
    assert list(session.scalars(select(ResponsibilityEvent)))==[]


def test_forget_rejects_stale_version(session):
    item=capture(session)
    with pytest.raises(s.Conflict): s.forget(session,item['id'],99)
    assert s.get(session,item['id'])


def test_reminders_catch_up_after_restart_without_repeated_notifications(session,session_factory):
    item=capture(session); r=follow(session,item['id'])
    s.save_preferences(session,Preferences(quiet_enabled=False)); session.commit()
    with session_factory() as restarted:
        inbox=s.poll(restarted,now=NOW+timedelta(days=2))
        assert [x['id'] for x in inbox['notify']]==[r['id']]
        s.mark_notified(restarted,[r['id']],now=NOW+timedelta(days=2)); restarted.commit()
    with session_factory() as restarted:
        inbox=s.poll(restarted,now=NOW+timedelta(days=3))
        assert inbox['notify']==[] and len(inbox['due'])==1
        s.change_reminder(restarted,r['id'],'acknowledge',now=NOW+timedelta(days=3)); restarted.commit()
        assert s.poll(restarted,now=NOW+timedelta(days=4))['due']==[]
        assert s.get(restarted,item['id']).status=='captured'


def test_snooze_rearms_once_and_acknowledgement_does_not_complete(session):
    item=capture(session); r=follow(session,item['id'])
    s.save_preferences(session,Preferences(quiet_enabled=False))
    s.poll(session,now=NOW+timedelta(hours=2)); s.mark_notified(session,[r['id']],now=NOW)
    later=NOW+timedelta(hours=4)
    s.change_reminder(session,r['id'],'snooze',later,now=NOW+timedelta(hours=2))
    assert s.poll(session,now=NOW+timedelta(hours=3))['due']==[]
    assert len(s.poll(session,now=later)['notify'])==1
    s.change_reminder(session,r['id'],'acknowledge',now=later)
    with pytest.raises(s.Conflict): s.change_reminder(session,r['id'],'snooze',later+timedelta(hours=1),now=later)
    assert s.get(session,item['id']).status=='captured'


def test_quiet_hours_keep_inbox_but_defer_notifications(session):
    item=capture(session); follow(session,item['id'])
    s.save_preferences(session,Preferences(timezone='Asia/Kolkata'))
    quiet=NOW.replace(hour=18)  # 23:30 local
    assert len(s.poll(session,now=quiet)['due'])==1
    assert s.poll(session,now=quiet)['notify']==[]
    assert len(s.poll(session,now=quiet+timedelta(hours=9))['notify'])==1


@pytest.mark.parametrize('zone,start,end,instant,quiet',[
    ('UTC','09:00','17:00','2026-10-20T09:00:00+00:00',True),
    ('UTC','09:00','17:00','2026-10-20T17:00:00+00:00',False),
    ('UTC','22:00','08:00','2026-10-20T08:00:00+00:00',False),
    ('UTC','22:00','08:00','2026-10-20T22:00:00+00:00',True),
    ('UTC','08:00','08:00','2026-10-20T08:00:00+00:00',False),
    ('America/New_York','01:00','03:00','2026-11-01T06:30:00+00:00',True)])
def test_quiet_hours_boundaries_and_dst(zone,start,end,instant,quiet):
    assert s.is_quiet(Preferences(timezone=zone,quiet_start=start,quiet_end=end),datetime.fromisoformat(instant)) is quiet


def test_pause_holds_overdue_reminders_until_resumed(session):
    item=capture(session); follow(session,item['id']); s.save_preferences(session,Preferences(quiet_enabled=False))
    s.patch(session,item['id'],ResponsibilityPatch(expected_version=1,status='paused'))
    assert s.poll(session,now=NOW+timedelta(days=1))['due']==[]
    s.patch(session,item['id'],ResponsibilityPatch(expected_version=2,status='active'))
    assert len(s.poll(session,now=NOW+timedelta(days=1))['due'])==1


def test_disabled_notifications_leave_followups_in_inbox(session):
    item=capture(session); follow(session,item['id'])
    s.save_preferences(session,Preferences(notifications_enabled=False))
    inbox=s.poll(session,now=NOW+timedelta(days=1))
    assert len(inbox['due'])==1 and inbox['notify']==[]


def test_reject_past_snooze_and_new_reminder(session):
    item=capture(session); r=follow(session,item['id'])
    with pytest.raises(ValueError,match='future'): follow(session,item['id'],NOW-timedelta(seconds=1))
    with pytest.raises(ValueError,match='future'): s.change_reminder(session,r['id'],'snooze',NOW,now=NOW)
