"""Czech language helpers: reflexive se/si particles, preposition forms.

Pure Python, no Qt. Two independent checkers used by the UI and the
read-only DB audit (``python -m slovnik.db_check``):

- ``check_reflexive(text)`` — validates the reflexive particle (se/si)
  of Czech verbs against a curated table of common verbs.
- ``check_prepositions(text)`` — catches the always-wrong forms of the
  alternating prepositions s/se, z/ze, k/ke, v/ve (e.g. "s mnou" instead
  of "se mnou", "ze Prahy" instead of "z Prahy").

``analyze_cs(text, checker)`` combines everything (plus a spelling pass)
into a list of :class:`Issue` objects; each mechanical fix carries a full
corrected replacement of the input text.

The reflexive table is intentionally small (~140 high-frequency verbs).
It is **conservative**: a verb is only flagged when the wrong particle is
unambiguous, so passive/impersonal uses ("Jak se to píše?", "Zde se
nepracuje", "voda se vaří") never produce false alarms. Verbs that allow
both particles with different meanings (``představit se/si``,
``půjčit/půjčit si``) carry a short note instead of a flag. Extend
``REFLEXIVES`` freely — each entry is one line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .spellcheck import SpellChecker, strip_diacritics

__all__ = [
    "Issue",
    "KIND_LABELS",
    "REFLEXIVES",
    "analyze_cs",
    "check_reflexive",
    "check_prepositions",
]


# --------------------------------------------------------------------------
# Reflexive verb table
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ReflexiveVerb:
    """Allowed reflexive particles for one verb (infinitive, no particle).

    particles: subset of {"se", "si", ""} — "" means the verb also occurs
    bare (transitive use: ``učit`` teach vs ``učit se`` learn).
    strict: if True, ANY particle outside ``particles`` is flagged even
    when "" is allowed (used when the wrong particle can only be an error).
    note: short Ukrainian explanation of the se/si meaning difference.
    """
    particles: tuple[str, ...]
    note: str = ""
    strict: bool = False


# Curated table of common Czech verbs. Key = infinitive WITHOUT the
# particle, lowercase (diacritics significant; lookup also tries a
# diacritic-stripped index, past-tense -l/-li and negated forms).
REFLEXIVES: dict[str, ReflexiveVerb] = {
    # --- strictly "se" (bare form and "si" are wrong) ---------------------
    "ptát":        ReflexiveVerb(("se",), "запитувати"),
    "ptávat":      ReflexiveVerb(("se",)),
    "zeptat":      ReflexiveVerb(("se",)),
    "zeptávat":    ReflexiveVerb(("se",)),
    "smát":        ReflexiveVerb(("se",), "сміятися"),
    "usmát":       ReflexiveVerb(("se",), "усміхнутися"),
    "usmívat":     ReflexiveVerb(("se",)),
    "zasmát":      ReflexiveVerb(("se",)),
    "bát":         ReflexiveVerb(("se",), "боятися"),
    "obávat":      ReflexiveVerb(("se",), "побоюватися"),
    "bojet":       ReflexiveVerb(("se",)),
    "dívat":       ReflexiveVerb(("se",), "дивитися"),
    "podívat":     ReflexiveVerb(("se",), "подивитися («podívat si» — розм.)"),
    "rozhlížet":   ReflexiveVerb(("se",), "роздивлятися"),
    "rozhlédnout": ReflexiveVerb(("se",), "роздивитися"),
    "ohlížet":     ReflexiveVerb(("se",), "озиратися"),
    "ohlédnout":   ReflexiveVerb(("se",), "озирнутися"),
    "snažit":      ReflexiveVerb(("se",), "намагатися"),
    "pokusit":     ReflexiveVerb(("se",), "спробувати"),
    "dařit":       ReflexiveVerb(("se",), "даватися: «daří se mi to»"),
    "podařit":     ReflexiveVerb(("se",), "вдатися: «podařilo se nám»"),
    "stydět":      ReflexiveVerb(("se",), "соромитися"),
    "hanbit":      ReflexiveVerb(("se",), "соромитися"),
    "chlubit":     ReflexiveVerb(("se",), "хвалитися"),
    "pochlubit":   ReflexiveVerb(("se",)),
    "pyšnit":      ReflexiveVerb(("se",), "пишатися"),
    "radovat":     ReflexiveVerb(("se",), "радітися"),
    "zdát":        ReflexiveVerb(("se",), "здаватися: «zdá se mi»"),
    "zříct":       ReflexiveVerb(("se",), "відмовитися (чогось)"),
    "zříkat":      ReflexiveVerb(("se",), "відмовлятися"),
    "starat":      ReflexiveVerb(("se",), "піклуватися: «starat se o»"),
    "postarat":    ReflexiveVerb(("se",), "подбати: «postarat se o»"),
    "zotavit":     ReflexiveVerb(("se",), "одужати, відновитися"),
    "vzpamatovat": ReflexiveVerb(("se",), "прийти до тями"),
    "ocitnout":    ReflexiveVerb(("se",), "опинитися"),
    "dozvědět":    ReflexiveVerb(("se",), "довідатися"),
    "dozvídat":    ReflexiveVerb(("se",), "довідуватися"),
    "ozvat":       ReflexiveVerb(("se",), "відкликнутися, подати голос"),
    "ozývat":      ReflexiveVerb(("se",)),
    "loučit":      ReflexiveVerb(("se",), "прощатися"),
    "rozloučit":   ReflexiveVerb(("se",), "попрощатися"),
    "zúčastnit":   ReflexiveVerb(("se",), "взяти участь: «zúčastnit se»"),
    "zúčastňovat": ReflexiveVerb(("se",)),
    "účastnit":    ReflexiveVerb(("se",)),
    "týkat":       ReflexiveVerb(("se",), "стосуватися: «týká se to…»"),
    "dotýkat":     ReflexiveVerb(("se",), "торкатися"),
    "dotknout":    ReflexiveVerb(("se",), "торкнутися"),
    "domnívat":    ReflexiveVerb(("se",), "вважати, гадати"),
    "vyvarovat":   ReflexiveVerb(("se",), "уникати: «vyvarovat se chyb»"),
    "mazlit":      ReflexiveVerb(("se",), "пеститися"),
    "tulit":       ReflexiveVerb(("se",), "притулятися, обійматися"),
    "zamilovat":   ReflexiveVerb(("se",), "закохатися"),

    # --- "se" or bare, different meanings; wrong "si" is flagged ----------
    "učit":        ReflexiveVerb(("", "se"), "učit — навчати (когось); učit se — вчитися", strict=True),
    "naučit":      ReflexiveVerb(("", "se"), "naučit — навчити (когось); naučit se — навчитися", strict=True),
    "cítit":       ReflexiveVerb(("", "se"), "cítit — відчувати (щось); cítit se — почувати себе", strict=True),
    "chovat":      ReflexiveVerb(("", "se"), "chovat — утримувати (тварин); chovat se — поводитися", strict=True),
    "zajímat":     ReflexiveVerb(("", "se"), "zajímat — цікавити (когось); zajímat se — цікавитися (чимось)", strict=True),
    "těšit":       ReflexiveVerb(("", "se"), "těšit — тішити; těšit se — чекати з нетерпінням", strict=True),
    "líbit":       ReflexiveVerb(("", "se"), "líbit se — подобатися: «líbí se mi to»", strict=True),
    "rozhodnout":  ReflexiveVerb(("", "se"), "rozhodnout — вирішити (щось); rozhodnout se — вирішитися", strict=True),
    "rozhodovat":  ReflexiveVerb(("", "se"), strict=True),
    "hádat":       ReflexiveVerb(("", "se"), "hádat — відгадувати; hádat se — сваритися", strict=True),
    "bít":         ReflexiveVerb(("", "se"), "bít — бити (когось); bít se — битися", strict=True),
    "potkat":      ReflexiveVerb(("", "se"), "potkat — зустріти (когось); potkat se — зустрітися", strict=True),
    "potkávat":    ReflexiveVerb(("", "se"), strict=True),
    "setkat":      ReflexiveVerb(("", "se"), "setkat se — зустрітися (з кимось)", strict=True),
    "setkávat":    ReflexiveVerb(("", "se"), strict=True),
    "vyhnout":     ReflexiveVerb(("", "se"), "vyhnout — прогнати; vyhnout se — уникати", strict=True),
    "vyhýbat":     ReflexiveVerb(("", "se"), strict=True),

    # --- "se" or bare, but "si" also exists → never flagged, only notes ---
    "stát":        ReflexiveVerb(("", "se"), "stát — стояти/коштувати; stát se — статися"),
    "vrátit":      ReflexiveVerb(("", "se"), "vrátit — повернути (щось); vrátit se — повернутися; vrátit si — повернути собі"),
    "vracet":      ReflexiveVerb(("", "se")),
    "dohodnout":   ReflexiveVerb(("", "se"), "dohodnout — узгодити; dohodnout se — домовитися"),
    "dostat":      ReflexiveVerb(("", "se"), "dostat — отримати; dostat se — дістатися (кудись)"),
    "dostávat":    ReflexiveVerb(("", "se")),
    "připojit":    ReflexiveVerb(("", "se"), "připojit — з'єднати; připojit se — приєднатися"),
    "projevit":    ReflexiveVerb(("", "se"), "projevit — виявити (щось); projevit se — виявитися"),
    "ukázat":      ReflexiveVerb(("", "se"), "ukázat — показати; ukázat se — показатися, з'явитися"),
    "objevit":     ReflexiveVerb(("", "se"), "objevit — відкрити; objevit se — з'явитися"),
    "ztratit":     ReflexiveVerb(("", "se"), "ztratit — загубити (щось); ztratit se — загубитися"),
    "ztrácet":     ReflexiveVerb(("", "se")),
    "nacházet":    ReflexiveVerb(("", "se"), "nacházet se — знаходитися"),
    "jmenovat":    ReflexiveVerb(("", "se"), "jmenovat se — зватися"),
    "zastavit":    ReflexiveVerb(("", "se"), "zastavit — зупинити; zastavit se — зупинитися"),
    "probudit":    ReflexiveVerb(("", "se"), "probudit — розбудити; probudit se — прокинутися"),
    "probouzet":   ReflexiveVerb(("", "se")),
    "vzbudit":     ReflexiveVerb(("", "se"), "vzbudit — розбудити; vzbudit se — прокинутися"),
    "vzbuzovat":   ReflexiveVerb(("", "se")),
    "stěhovat":    ReflexiveVerb(("", "se"), "stěhovat se — переїжджати"),
    "přestěhovat": ReflexiveVerb(("", "se"), "přestěhovat se — переїхати"),
    "nastěhovat":  ReflexiveVerb(("", "se"), "nastěhovat se — заселитися"),
    "vydat":       ReflexiveVerb(("", "se"), "vydat — видати; vydat se — вирушити"),
    "vypravit":    ReflexiveVerb(("", "se"), "vypravit se — вирушити (в дорогу)"),
    "dostavit":    ReflexiveVerb(("", "se"), "dostavit se — прибути (офіційно)"),
    "svěřit":      ReflexiveVerb(("", "se"), "svěřit — довірити; svěřit se — поділитися, зізнатися"),
    "vyznat":      ReflexiveVerb(("", "se"), "vyznat se — добре орієнтуватися: «vyznat se v hudbě»"),
    "zdravit":     ReflexiveVerb(("", "se"), "zdravit — вітати (когось); zdravit se — вітатися"),
    "hlásit":      ReflexiveVerb(("", "se"), "hlásit — повідомляти; hlásit se — звітувати, записуватися"),
    "přihlásit":   ReflexiveVerb(("", "se"), "přihlásit se — зареєструватися"),
    "odhlásit":    ReflexiveVerb(("", "se"), "odhlásit se — вийти (з акаунта)"),
    "vdát":        ReflexiveVerb(("", "se"), "vdát se — вийти заміж"),
    "vdávat":      ReflexiveVerb(("", "se")),
    "oženit":      ReflexiveVerb(("", "se"), "oženit se — одружитися"),
    "ženit":       ReflexiveVerb(("", "se")),
    "narodit":     ReflexiveVerb(("", "se"), "narodit se — народитися"),
    "milovat":     ReflexiveVerb(("", "se"), "milovat — любити; milovat se — кохатися"),
    "řídit":       ReflexiveVerb(("", "se"), "řídit — керувати; řídit se — керуватися: «řídit se pravidly»"),
    "opřít":       ReflexiveVerb(("", "se"), "opřít se — обпертися"),
    "opírat":      ReflexiveVerb(("", "se")),
    "protáhnout":  ReflexiveVerb(("", "se"), "protáhnout — протягнути; protáhnout se — потягнутися, пройтися"),
    "poškrábat":   ReflexiveVerb(("", "se"), "poškrábat — подряпати; poškrábat se — подряпатися"),
    "zranit":      ReflexiveVerb(("", "se"), "zranit — поранити (когось); zranit se — поранитися"),
    "utopit":      ReflexiveVerb(("", "se"), "utopit — утопити; utopit se — утопитися"),
    "koupat":      ReflexiveVerb(("", "se"), "koupat — купати (когось); koupat se — купатися"),
    "sprchovat":   ReflexiveVerb(("", "se"), "sprchovat se — приймати душ"),
    "seznámit":    ReflexiveVerb(("", "se"), "seznámit — познайомити; seznámit se — познайомитися"),
    "omluvit":     ReflexiveVerb(("", "se"), "omluvit — вибачити (когось); omluvit se — вибачитися"),
    "ospravedlnit": ReflexiveVerb(("", "se"), "ospravedlnit se — вибачитися (офіційно)"),
    "přizpůsobit": ReflexiveVerb(("", "se"), "přizpůsobit se — пристосуватися"),
    "podřídit":    ReflexiveVerb(("", "se"), "podřídit se — підкоритися"),
    "soustředit":  ReflexiveVerb(("", "se"), "soustředit se — зосередитися (на чомусь)"),
    "chránit":     ReflexiveVerb(("", "se"), "chránit — захищати; chránit se — захищатися"),
    "bránit":      ReflexiveVerb(("", "se"), "bránit — заважати; bránit se — оборонятися"),
    "obhájit":     ReflexiveVerb(("", "se"), "obhájit — захистити (диплом); obhájit se — виправдатися"),
    "otočit":      ReflexiveVerb(("", "se"), "otočit — повернути (щось); otočit se — обернутися"),
    "přiblížit":   ReflexiveVerb(("", "se"), "přiblížit — наблизити; přiblížit se — наблизитися"),
    "ubytovat":    ReflexiveVerb(("", "se"), "ubytovat se — розміститися, оселитися"),
    "pokoušet":    ReflexiveVerb(("", "se"), "pokoušet — спокушати; pokoušet se — намагатися"),
    "stavět":      ReflexiveVerb(("", "se"), "stavět — будувати; «dům se staví» — пасив"),

    # --- strictly "si" ------------------------------------------------------
    "všimnout":    ReflexiveVerb(("si",), "všimnout si — помітити (тільки «si»!)"),
    "všímat":      ReflexiveVerb(("si",)),
    "uvědomit":    ReflexiveVerb(("si",), "uvědomit si — усвідомити (тільки «si»!)"),
    "uvědomovat":  ReflexiveVerb(("si",)),

    # --- "si" or bare; wrong "se" is flagged --------------------------------
    "sednout":     ReflexiveVerb(("", "si"), "sednout si — сісти", strict=True),
    "sedat":       ReflexiveVerb(("", "si"), strict=True),
    "lehnout":     ReflexiveVerb(("", "si"), "lehnout si — лягти", strict=True),
    "lehat":       ReflexiveVerb(("", "si"), strict=True),
    "kleknout":    ReflexiveVerb(("", "si"), "kleknout si — стати на коліна", strict=True),
    "dřepnout":    ReflexiveVerb(("", "si"), "dřepnout si — присісти", strict=True),
    "zvyknout":    ReflexiveVerb(("", "si"), "zvyknout si — звикнути; zvyknout — привчити (когось)", strict=True),
    "zvykat":      ReflexiveVerb(("", "si"), strict=True),

    # --- bare only; any particle is flagged ---------------------------------
    "vstát":       ReflexiveVerb(("",), "встати (без частки)", strict=True),
    "usnout":      ReflexiveVerb(("",), "заснути (без частки)", strict=True),
    "zůstat":      ReflexiveVerb(("",), "залишитися (без частки)", strict=True),

    # --- "si" or bare, notes only (passive "se" is legitimate) --------------
    "myslet":      ReflexiveVerb(("", "si"), "myslet — думати; myslet si — вважати: «myslím si, že…»"),
    "pamatovat":   ReflexiveVerb(("", "si"), "pamatovat si — пам'ятати (щось); pamatovat na — пам'ятати про"),
    "vzpomínat":   ReflexiveVerb(("", "si"), "vzpomínat na — згадувати"),
    "vzpomenout":  ReflexiveVerb(("", "si")),
    "stěžovat":    ReflexiveVerb(("", "si"), "stěžovat si — скаржитися"),
    "půjčit":      ReflexiveVerb(("", "si"), "půjčit — позичити (комусь); půjčit si — позичити (у когось)"),
    "půjčovat":    ReflexiveVerb(("", "si")),
    "vypůjčit":    ReflexiveVerb(("", "si"), "vypůjčit si — взяти напрокат/позичити"),
    "najít":       ReflexiveVerb(("", "si"), "najít — знайти; najít si — знайти собі: «najít si čas»"),
    "najmout":     ReflexiveVerb(("", "si"), "najmout si — найняти (собі)"),
    "pronajmout":  ReflexiveVerb(("", "si"), "pronajmout — здати в оренду; pronajmout si — орендувати"),
    "troufnout":   ReflexiveVerb(("", "si"), "troufnout si — наважитися"),
    "dovolit":     ReflexiveVerb(("", "si"), "dovolit — дозволити; dovolit si — дозволити собі"),
    "udělat":      ReflexiveVerb(("", "si"), "udělat si — зробити собі: «udělat si čas»"),
    "dělat":       ReflexiveVerb(("", "si"), "dělat si legraci (z někoho) — жартувати (з когось)"),
    "odpočinout":  ReflexiveVerb(("", "si"), "odpočinout si — відпочити"),
    "ublížit":     ReflexiveVerb(("", "si"), "ublížit (někomu) — зашкодити; ublížit si — зашкодити собі"),
    "koupit":      ReflexiveVerb(("", "si"), "koupit — купити; koupit si — купити собі"),
    "kupovat":     ReflexiveVerb(("", "si")),
    "objednat":    ReflexiveVerb(("", "si"), "objednat — замовити; objednat si — замовити собі"),
    "objednávat":  ReflexiveVerb(("", "si")),

    # --- all three forms occur; entries exist for the meaning notes ---------
    "dát":         ReflexiveVerb(("", "se", "si"), "dát si — узяти (напій): «dát si kávu»; dát se — взятися: «dát se do práce»"),
    "vzít":        ReflexiveVerb(("", "se", "si"), "vzít — взяти; vzít si — одружитися (з кимось); vzít se — взяти шлюб"),
    "brát":        ReflexiveVerb(("", "se", "si")),
    "chystat":     ReflexiveVerb(("", "se", "si"), "chystat — готувати; chystat se — збиратися (щось зробити); chystat si — готувати собі"),
    "připravit":   ReflexiveVerb(("", "se", "si"), "připravit — підготувати; připravit se — підготуватися; připravit si — підготувати собі"),
    "postavit":    ReflexiveVerb(("", "se", "si"), "postavit — поставити/збудувати; postavit se — встати; postavit si — збудувати собі"),
    "posadit":     ReflexiveVerb(("", "se", "si"), "posadit — посадити (когось); posadit se — сісти"),
    "obléknout":   ReflexiveVerb(("", "se", "si"), "obléknout — одягнути; obléknout se — одягнутися; obléknout si — одягнути (на себе)"),
    "svléknout":   ReflexiveVerb(("", "se", "si"), "svléknout se — роздягнутися; svléknout si — зняти (з себе)"),
    "obout":       ReflexiveVerb(("", "se", "si"), "obout si — взути (черевики); obout se — взутися"),
    "umýt":        ReflexiveVerb(("", "se", "si"), "umýt — вимити (щось); umýt se — вимитися; umýt si ruce — вимити руки"),
    "česat":       ReflexiveVerb(("", "se", "si"), "česat — чесати; česat se — чесатися; česat si vlasy — чесати волосся"),
    "holit":       ReflexiveVerb(("", "se", "si"), "holit se — бритися; holit si bradu — голити бороду"),
    "představit":  ReflexiveVerb(("", "se", "si"), "představit — представити (когось); představit se — представитися; představit si — уявити"),
    "přiznat":     ReflexiveVerb(("", "se", "si"), "přiznat — визнати (щось); přiznat se — зізнатися; přiznat si — визнати (собі)"),
    "domluvit":    ReflexiveVerb(("", "se", "si"), "domluvit se — домовитися / розмовляти (мовою); domluvit si — домовитися (про зустріч)"),
    "vážit":       ReflexiveVerb(("", "se", "si"), "vážit — зважувати; vážit si — шанувати: «vážím si tě»; vážit se — важити"),
    "zasloužit":   ReflexiveVerb(("", "se", "si"), "zasloužit si — заслужити; zasloužit se (o) — зробити внесок"),
    "zapsat":      ReflexiveVerb(("", "se", "si"), "zapsat — записати; zapsat si — записати собі; zapsat se — записатися (на курс)"),
    "stáhnout":    ReflexiveVerb(("", "se", "si"), "stáhnout — стягнути/завантажити; stáhnout si — завантажити собі; stáhnout se — відступити"),
    "vysvětlit":   ReflexiveVerb(("", "se", "si"), "vysvětlit — пояснити (комусь); vysvětlit si — пояснити собі; vysvětlit se — виправдатися"),
    "zapomenout":  ReflexiveVerb(("", "se", "si"), "zapomenout — забути; zapomenout si na — забути про"),
    "obnovit":     ReflexiveVerb(("", "se", "si"), "obnovit — відновити; obnovit si — відновити (собі): «obnovit si paměť»"),
    "ověřit":      ReflexiveVerb(("", "se", "si"), "ověřit — перевірити; ověřit si — перевірити (собі)"),
    "uložit":      ReflexiveVerb(("", "se", "si"), "uložit — зберегти; uložit si — зберегти (собі)"),
    "nastavit":    ReflexiveVerb(("", "se", "si"), "nastavit — налаштувати; nastavit si — налаштувати (собі)"),
}

_STRIPPED_INDEX: dict[str, str] | None = None


def _stripped_index() -> dict[str, str]:
    """Diacritic-stripped lookup index: 'ptat' → 'ptát' (first key wins)."""
    global _STRIPPED_INDEX
    if _STRIPPED_INDEX is None:
        idx: dict[str, str] = {}
        for key in REFLEXIVES:
            idx.setdefault(strip_diacritics(key), key)
        _STRIPPED_INDEX = idx
    return _STRIPPED_INDEX


# --------------------------------------------------------------------------
# Issues
# --------------------------------------------------------------------------

@dataclass
class Issue:
    """One finding about a ``cs`` text, with an optional mechanical fix."""
    kind: str                  # "spelling" | "reflexive" | "preposition" | "alphabet"
    severity: str              # "error" | "warn" | "info"
    token: str = ""            # fragment the issue is about
    message: str = ""          # human-readable (Ukrainian)
    suggestion: str | None = None  # full corrected text, if mechanically fixable


KIND_LABELS = {
    "spelling": "орфографія",
    "reflexive": "se/si",
    "preposition": "прийменник",
    "alphabet": "алфавіт",
}


# --------------------------------------------------------------------------
# Tokenization
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\S+")
_STRIP_CHARS = "«»\"'`.,!?():;—–"
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_VOWELS = set("aeiouyáéíóúůýě")
_AUX = {"budu", "budeš", "bude", "budeme", "budete", "budou",
        "bych", "bys", "by", "bychom", "byste"}


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text or "")


def _core(token: str) -> str:
    """Lowercased word with edge punctuation removed."""
    return token.strip(_STRIP_CHARS).lower()


def _lookup_verb(verb: str) -> tuple[str, ReflexiveVerb] | tuple[None, None]:
    """Match a token against REFLEXIVES.

    Tries, in order: exact form, diacritic-stripped form, past tense
    (``ptal`` → ``ptat``, ``ptali`` → ``ptat``), 3rd-person present
    (``líbí`` → ``líbít`` via the stripped index), then negation
    (``neptát`` → ``ptát``).
    """
    v = (verb or "").strip().lower()
    if not v:
        return None, None
    if v in REFLEXIVES:
        return v, REFLEXIVES[v]
    idx = _stripped_index()
    key = idx.get(strip_diacritics(v))
    if key:
        return key, REFLEXIVES[key]
    # past tense: "ptal" → "ptat", "ptali" → "ptat"
    if v.endswith("li"):
        cand = v[:-2] + "t"
    elif v.endswith("l"):
        cand = v[:-1] + "t"
    else:
        cand = None
    if cand:
        if cand in REFLEXIVES:
            return cand, REFLEXIVES[cand]
        key = idx.get(strip_diacritics(cand))
        if key:
            return key, REFLEXIVES[key]
    # 3rd-person present: "líbí" → "líbít" (stripped match only)
    key = idx.get(strip_diacritics(v + "t"))
    if key:
        return key, REFLEXIVES[key]
    # 1st/2nd-person endings → infinitive: "učím"/"učíš" → "učít",
    # "učíme"/"učíte" → "učít" (stripped match only)
    for suffix in ("me", "te", "m", "š"):
        if v.endswith(suffix) and len(v) > len(suffix):
            key = idx.get(strip_diacritics(v[: -len(suffix)] + "t"))
            if key:
                return key, REFLEXIVES[key]
    # negation: "neptát" → "ptát"
    if v.startswith("ne") and len(v) > 3:
        return _lookup_verb(v[2:])
    return None, None


def _note(info: ReflexiveVerb) -> str:
    return f" ({info.note})" if info.note else ""


def _join(toks: list[str]) -> str:
    return " ".join(toks)


def _insert_particle(toks: list[str], verb_idx: int, particle: str) -> str:
    """Build the corrected text with the particle added.

    Czech clitics take the second syntactic position, so the particle
    goes after the first token ("chci zeptat" → "chci se zeptat",
    "budu ptát" → "budu se ptát") — or right after a lone verb.
    """
    out = list(toks)
    if len(out) == 1 or verb_idx == 0:
        out.insert(verb_idx + 1, particle)
        return _join(out)
    prev = _core(out[verb_idx - 1])
    if prev in _AUX:
        out.insert(verb_idx, particle)  # "budu ptát" → "budu se ptát"
    else:
        out.insert(1, particle)         # "chci zeptat" → "chci se zeptat"
    return _join(out)


def _expected_particle(info: ReflexiveVerb) -> str:
    return "se" if "se" in info.particles else "si"


# --------------------------------------------------------------------------
# Reflexive check
# --------------------------------------------------------------------------

def check_reflexive(text: str) -> Issue | None:
    """Validate se/si particles; return an Issue or None if fine/unknown."""
    toks = _tokens(text)
    if not toks:
        return None
    cores = [_core(t) for t in toks]

    p_idx = next((i for i, c in enumerate(cores) if c in ("se", "si")), None)
    if p_idx is None:
        # No particle anywhere: flag strictly-reflexive verbs used bare.
        for i, c in enumerate(cores):
            if not c:
                continue
            _, info = _lookup_verb(c)
            if info is None or "" in info.particles:
                continue
            expected = _expected_particle(info)
            return Issue(
                kind="reflexive", severity="error", token=toks[i],
                message=(f"«{toks[i]}» зазвичай вимагає частку «{expected}»."
                         f" Правильно: «{_insert_particle(toks, i, expected)}»."
                         f"{_note(info)}"),
                suggestion=_insert_particle(toks, i, expected),
            )
        return None

    used = cores[p_idx]
    # The verb sits next to the particle; skip over auxiliaries
    # ("budu se ptát") and accept the colloquial fronted order ("se ptám").
    verb_idx: int | None = None
    if p_idx > 0 and cores[p_idx - 1] not in _AUX:
        verb_idx = p_idx - 1
    elif p_idx > 0 and p_idx + 1 < len(cores):
        verb_idx = p_idx + 1
    elif p_idx + 1 < len(cores):
        verb_idx = p_idx + 1
    if verb_idx is None:
        return None

    _, info = _lookup_verb(cores[verb_idx])
    if info is None or used in info.particles:
        return None
    if "" in info.particles and not info.strict:
        # Bare use exists and passive/impersonal "se" is possible — no flag.
        return None
    if "se" in info.particles:
        expected = "se"
    elif "si" in info.particles:
        expected = "si"
    else:
        # The verb never takes a particle ("vstát si") — drop it.
        fixed = [t for j, t in enumerate(toks) if j != p_idx]
        return Issue(
            kind="reflexive", severity="error",
            token=f"{toks[verb_idx]} {toks[p_idx]}",
            message=(f"«{toks[verb_idx]} {used}» — помилка: дієслово вживається "
                     f"без частки. Правильно: «{_join(fixed)}».{_note(info)}"),
            suggestion=_join(fixed),
        )
    fixed = list(toks)
    fixed[p_idx] = expected
    return Issue(
        kind="reflexive", severity="error",
        token=f"{toks[verb_idx]} {toks[p_idx]}",
        message=(f"«{toks[verb_idx]} {used}» — найімовірніше помилка."
                 f" Правильно: «{toks[verb_idx]} {expected}».{_note(info)}"),
        suggestion=_join(fixed),
    )


# --------------------------------------------------------------------------
# Preposition check (s/se, z/ze, k/ke, v/ve)
# --------------------------------------------------------------------------

_SHORT_TO_LONG = {"s": "se", "z": "ze", "k": "ke", "v": "ve"}
# Reverse (long→short) check only runs for unambiguous long forms; "se"
# is skipped because it doubles as the reflexive particle ("zeptal se
# Petra" — bare genitive, not a preposition).
_LONG_KEEP: dict[str, set[str]] = {
    # consonants after which the long form is correct
    "ze": set("zšžs"),
    "ke": {"k"},
    "ve": {"v"},
}


def check_prepositions(text: str) -> list[Issue]:
    """Catch always-wrong preposition forms, e.g. 's mnou' → 'se mnou'."""
    toks = _tokens(text)
    issues: list[Issue] = []
    for i in range(len(toks) - 1):
        c = _core(toks[i])
        nxt_raw = toks[i + 1]
        nxt = _core(nxt_raw)
        if not nxt:
            continue
        if c in _SHORT_TO_LONG:
            long = _SHORT_TO_LONG[c]
            # "s mnou"/"s sebou"/"k mně"/"v mně" and same-letter doubles
            # ("s sestrou", "z zahrady", "k koni", "v vlaku") are always wrong.
            wrong = (
                (c == "s" and nxt.startswith(("mnou", "sebou")))
                or (c in ("k", "v") and nxt == "mně")
                or (nxt[0] == c)
            )
            if wrong:
                fixed = list(toks)
                fixed[i] = long
                issues.append(Issue(
                    kind="preposition", severity="error", token=toks[i],
                    message=(f"Прийменник: правильно «{long} {nxt_raw}» — "
                             f"коротка форма «{toks[i]}» тут неможлива."),
                    suggestion=_join(fixed),
                ))
        elif c in _LONG_KEEP:
            short = c[:-1]  # ze→z, ke→k, ve→v
            first = nxt[0].lower()
            second = nxt[1].lower() if len(nxt) > 1 else ""
            if not first.isalpha():
                continue
            # Long form is wrong before a vowel ("ze okna") or before a
            # single non-keep consonant ("ze Prahy", "ke tobě"). Initial
            # consonant clusters ("ze dvora", "ve čtvrtek") keep it.
            if first in _VOWELS or (
                first not in _LONG_KEEP[c] and second in _VOWELS
            ):
                fixed = list(toks)
                fixed[i] = short
                issues.append(Issue(
                    kind="preposition", severity="error", token=toks[i],
                    message=(f"Прийменник: правильно «{short} {nxt_raw}» — "
                             f"довга форма «{toks[i]}» тут не вживається."),
                    suggestion=_join(fixed),
                ))
    return issues


# --------------------------------------------------------------------------
# Combined analysis
# --------------------------------------------------------------------------

_MAX_SPELLING_ISSUES = 5


def _derived_forms(word: str) -> list[str]:
    """Likely dictionary base forms for a derived Czech word (heuristic).

    The pure-Python spell backend knows only base forms — it does not
    apply hunspell affix rules — so derived adverbs/nouns look unknown.
    These transformations cover the productive Czech derivations;
    a real typo only survives if its transformed stem is a real word.
    """
    cands: list[str] = []
    if word.endswith("ně"):
        stem = word[:-2]
        cands += [stem + "ný", stem + "ní"]     # nutně → nutný
    if word.endswith("ost"):
        stem = word[:-3]
        cands += [stem + s for s in ("ý", "á", "í", "ě")]
        # společnost → společný, přednost → přední
    if word.endswith("ě") or word.endswith("e"):
        cands += [word[:-1] + s for s in ("ý", "í", "á")]  # rychle → rychlý
    if word.endswith("y"):
        cands.append(word[:-1] + "ý")           # matematicky → matematický
    if word.endswith("o"):
        cands.append(word[:-1])                 # uvedeno → uveden
    return cands


def _likely_derived(word: str, checker: SpellChecker) -> bool:
    return any(checker.is_correct(c) for c in _derived_forms(word))


def analyze_cs(text: str, checker: SpellChecker | None = None) -> list[Issue]:
    """Run all Czech checks on a ``cs`` field value.

    ``checker`` (a SpellChecker) enables the spelling pass; with None only
    the rule-based checks (reflexive, prepositions, alphabet) run.
    """
    text = (text or "").strip()
    issues: list[Issue] = []
    if not text:
        return issues

    if _CYRILLIC_RE.search(text):
        issues.append(Issue(
            kind="alphabet", severity="error",
            message="У полі «cs» кирилиця — схоже, переклад записано не в тому полі.",
        ))

    if checker is not None:
        flagged = 0
        seen: set[str] = set()
        for tok in _tokens(text):
            core = _core(tok)
            if len(core) < 2 or core in seen:
                continue
            seen.add(core)
            if (checker.is_correct(core) or checker.is_correct(core.lower())
                    or _likely_derived(core, checker)):
                continue
            sugg = [s for s in checker.suggest(core, 5) if s != core][:3]
            issues.append(Issue(
                kind="spelling", severity="warn", token=tok,
                message=(f"Можлива помилка у слові «{tok}»."
                         + (f" Схоже на: {', '.join(sugg)}." if sugg else "")),
            ))
            flagged += 1
            if flagged >= _MAX_SPELLING_ISSUES:
                break

    refl = check_reflexive(text)
    if refl:
        issues.append(refl)
    issues.extend(check_prepositions(text))
    return issues
