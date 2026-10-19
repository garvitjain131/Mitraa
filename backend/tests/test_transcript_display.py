from mitraa.assistant.transcript_display import romanize_hindi

def test_hindi_display():
    assert romanize_hindi('तुम्हारा नाम क्या है?')=='tumhaaraa naam kyaa hai?'

def test_mixed_display_preserves_english_and_paths():
    assert romanize_hindi('मेरी file Downloads/Resume.pdf कहाँ है?')=='meri file Downloads/Resume.pdf kahaan hai?'

def test_english_unchanged():
    assert romanize_hindi('Hello Mitraa! Open C:/My Files.')=='Hello Mitraa! Open C:/My Files.'
