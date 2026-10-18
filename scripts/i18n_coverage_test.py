"""Every Thai phrase the pages can show has an English entry in app/static/i18n/en.json.

ui.js translates a rendered text all-or-nothing: one Thai piece without an entry keeps the whole text Thai,
so a missing entry shows up in English mode as a Thai sentence. This mirrors that check over the page sources.
"""
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / 'app' / 'static'
PAGES = sorted(p for p in [*STATIC.glob('*.html'), *STATIC.glob('*.js')]
               if p.name not in ('hyperframes-player.js',))
THAI = re.compile('[฀-๿]')
# a run of text between quotes, tags, template holes and line ends that holds Thai
RUN = re.compile(r"[^<>'\"`\n{}]*[฀-๿][^<>'\"`\n{}]*")
COMMENT = re.compile(r'/\*.*?\*/|<!--.*?-->|(?:^|(?<=\s))//[^\n]*', re.S)

# Thai on purpose: the language switch names Thai in Thai, and ui.js holds the Thai letter range itself
KEEP = {('login.js', 'ภาษาไทย'), ('shell.js', 'ภาษาไทย'), ('ui.js', 'const TH = /[฀-๿]/, ATTRS = [')}


def untranslated():
    table = json.loads((STATIC / 'i18n' / 'en.json').read_text(encoding='utf-8'))
    keys = sorted(table, key=len, reverse=True)
    phrase = re.compile('|'.join(re.escape(k) for k in keys))
    missing = []
    for page in PAGES:
        source = COMMENT.sub(lambda m: re.sub(r'[^\n]', ' ', m.group()), page.read_text(encoding='utf-8'))
        for number, line in enumerate(source.split('\n'), 1):
            for run in RUN.findall(line):
                run = run.strip()
                if (page.name, run) not in KEEP and THAI.search(phrase.sub('', run)):
                    missing.append(f'{page.name}:{number}: {run}')
    return missing


def main():
    table = json.loads((STATIC / 'i18n' / 'en.json').read_text(encoding='utf-8'))
    assert all(THAI.search(k) and v.strip() for k, v in table.items()), 'every entry maps Thai to non-empty English'
    missing = untranslated()
    assert not missing, f'{len(missing)} Thai texts have no English entry in i18n/en.json:\n' + '\n'.join(missing)
    print(f'i18n coverage OK: {len(PAGES)} pages, {len(table)} entries')


if __name__ == '__main__':
    main()
