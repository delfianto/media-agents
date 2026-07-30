# ISO 639-1/639-2 code -> display name, covering codes actually seen in
# Plex/Matroska/MP4 media libraries. Falls back gracefully for anything missing.
NAMES = {
    "aar": "Afar",
    "abk": "Abkhazian",
    "afr": "Afrikaans",
    "amh": "Amharic",
    "ara": "Arabic",
    "ar": "Arabic",
    "arm": "Armenian",
    "hye": "Armenian",
    "aze": "Azerbaijani",
    "baq": "Basque",
    "eus": "Basque",
    "bel": "Belarusian",
    "ben": "Bengali",
    "bos": "Bosnian",
    "bul": "Bulgarian",
    "bg": "Bulgarian",
    "bur": "Burmese",
    "mya": "Burmese",
    "cat": "Catalan",
    "ces": "Czech",
    "cze": "Czech",
    "cs": "Czech",
    "chi": "Chinese",
    "zho": "Chinese",
    "zh": "Chinese",
    "cnr": "Montenegrin",
    "cpe": "Creole",
    "hrv": "Croatian",
    "hr": "Croatian",
    "dan": "Danish",
    "da": "Danish",
    "dut": "Dutch",
    "nld": "Dutch",
    "nl": "Dutch",
    "eng": "English",
    "en": "English",
    "epo": "Esperanto",
    "est": "Estonian",
    "et": "Estonian",
    "fil": "Filipino",
    "fin": "Finnish",
    "fi": "Finnish",
    "fre": "French",
    "fra": "French",
    "fr": "French",
    "geo": "Georgian",
    "kat": "Georgian",
    "ger": "German",
    "deu": "German",
    "de": "German",
    "gre": "Greek",
    "ell": "Greek",
    "el": "Greek",
    "guj": "Gujarati",
    "heb": "Hebrew",
    "he": "Hebrew",
    "hin": "Hindi",
    "hi": "Hindi",
    "hun": "Hungarian",
    "hu": "Hungarian",
    "ice": "Icelandic",
    "isl": "Icelandic",
    "ind": "Indonesian",
    "id": "Indonesian",
    "gle": "Irish",
    "ita": "Italian",
    "it": "Italian",
    "jpn": "Japanese",
    "ja": "Japanese",
    "kan": "Kannada",
    "kaz": "Kazakh",
    "khm": "Khmer",
    "kor": "Korean",
    "ko": "Korean",
    "kur": "Kurdish",
    "lav": "Latvian",
    "lv": "Latvian",
    "lit": "Lithuanian",
    "lt": "Lithuanian",
    "mac": "Macedonian",
    "mkd": "Macedonian",
    "may": "Malay",
    "msa": "Malay",
    "ms": "Malay",
    "mal": "Malayalam",
    "mar": "Marathi",
    "mon": "Mongolian",
    "nep": "Nepali",
    "nor": "Norwegian",
    "no": "Norwegian",
    "nob": "Norwegian Bokmal",
    "nno": "Norwegian Nynorsk",
    "per": "Persian",
    "fas": "Persian",
    "fa": "Persian",
    "pol": "Polish",
    "pl": "Polish",
    "por": "Portuguese",
    "pt": "Portuguese",
    "pan": "Punjabi",
    "rum": "Romanian",
    "ron": "Romanian",
    "ro": "Romanian",
    "rus": "Russian",
    "ru": "Russian",
    "srp": "Serbian",
    "sr": "Serbian",
    "sin": "Sinhala",
    "slo": "Slovak",
    "slk": "Slovak",
    "sk": "Slovak",
    "slv": "Slovenian",
    "sl": "Slovenian",
    "spa": "Spanish",
    "es": "Spanish",
    "swa": "Swahili",
    "swe": "Swedish",
    "sv": "Swedish",
    "tam": "Tamil",
    "ta": "Tamil",
    "tel": "Telugu",
    "te": "Telugu",
    "tha": "Thai",
    "th": "Thai",
    "tur": "Turkish",
    "tr": "Turkish",
    "ukr": "Ukrainian",
    "uk": "Ukrainian",
    "urd": "Urdu",
    "vie": "Vietnamese",
    "vi": "Vietnamese",
    "wel": "Welsh",
    "cym": "Welsh",
    "yid": "Yiddish",
    "yue": "Cantonese",
    "zxx": "No linguistic content",
    "und": "Undetermined",
    "mul": "Multiple languages",
}

ENGLISH_CODES = {"eng", "en"}
JAPANESE_CODES = {"jpn", "ja"}
UNKNOWN_CODES = {"und", "unk", "mis", "mul", "zxx", "", None}


def base_subtag(code):
    """Reduce an IETF-ish tag like 'es-419' or 'zh-Hans' to its primary subtag."""
    if not code:
        return code
    return code.split("-")[0].lower()


def display_name(code):
    if not code:
        return "Unknown"
    key = code.lower()
    if key in NAMES:
        return NAMES[key]
    base = base_subtag(key)
    if base in NAMES:
        return NAMES[base]
    return code


def is_english(code):
    if not code:
        return False
    key = code.lower()
    return key in ENGLISH_CODES or base_subtag(key) in ENGLISH_CODES


def is_japanese(code):
    if not code:
        return False
    key = code.lower()
    return key in JAPANESE_CODES or base_subtag(key) in JAPANESE_CODES


def is_unknown(code):
    if code is None:
        return True
    key = code.lower()
    return key in UNKNOWN_CODES or base_subtag(key) in UNKNOWN_CODES
