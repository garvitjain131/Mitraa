from datetime import datetime,timedelta,timezone
from mitraa.agent.orchestrator import Orchestrator
from mitraa.responsibilities import service
from mitraa.responsibilities.schemas import ResponsibilityCreate
from tests.fakes import FakeLLM,act,call,say


def test_agent_can_capture_responsibility_with_audited_tool(session,settings,sandbox):
    llm=FakeLLM([act(call('create_responsibility',{'title':'Application','outcome':'Submit reviewed application',
        'next_action':'Check requirements','timezone':'Asia/Kolkata'})),say('Saved your responsibility.')])
    result=Orchestrator(llm,session,settings,sandbox=sandbox).run(None,'Help me track this application')
    assert result.steps[0].tool=='create_responsibility' and result.steps[0].status=='success'
    assert service.list_items(session)[0]['next_action']=='Check requirements'


def test_agent_updates_using_current_version_and_creates_followup(session,settings,sandbox):
    item=service.create(session,ResponsibilityCreate(title='Interview')); session.commit()
    when=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    llm=FakeLLM([act(call('list_responsibilities')),act(call('update_responsibility',{
        'responsibility_id':item['id'],'expected_version':1,'next_action':'Review the role','status':'active'}),
        call('add_reminder',{'responsibility_id':item['id'],'message':'Review the role','due_at':when})),
        say('Your next action and reminder are saved.')])
    result=Orchestrator(llm,session,settings,sandbox=sandbox).run(None,'Remind me tomorrow at the agreed time')
    assert all(step.status=='success' for step in result.steps)
    assert service.list_items(session)[0]['version']==2
    assert service.list_reminders(session)[0]['message']=='Review the role'
