"""Local English-letter display for Hindi, preserving other scripts/text."""
import re

def romanize_hindi(text: str) -> str:
    if not re.search(r'[\u0900-\u097f]',text):
        return text
    from indic_transliteration import sanscript
    def word(match):
        original=match.group()
        result=sanscript.transliterate(original,sanscript.DEVANAGARI,sanscript.ITRANS)
        # Hindi normally drops a final inherent schwa (नाम -> naam).
        if re.search(r'[क-हक़-य़]$',original) and result.endswith('a') and len(re.findall('[aAiIuUeEoO]',result))>1:
            result=result[:-1]
        for source,target in [('RRi','ri'),('RRI','ri'),('~N','n'),('~n','ny'),('Sh','sh'),('.N','n'),('.n','n'),('A','aa'),('I','i'),('U','u'),('M','n'),('N','n')]:
            result=result.replace(source,target)
        return result.lower().replace('।','.').replace('॥','.')
    return re.sub(r'[\u0900-\u097f]+',word,text)
