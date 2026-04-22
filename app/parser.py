# app/parser.py
import re
import pycountry

# Only adjectives — pycountry handles actual country names automatically
# Just in case adjectives like "nigerian" is typed
ADJECTIVES = {
    "nigerian": "NG",
    "ghanaian": "GH",
    "kenyan": "KE",
    "tanzanian": "TZ",
    "ugandan": "UG",
    "ethiopian": "ET",
    "south african": "ZA",
    "egyptian": "EG",
    "cameroonian": "CM",
    "senegalese": "SN",
    "angolan": "AO",
    "mozambican": "MZ",
    "zambian": "ZM",
    "zimbabwean": "ZW",
    "malawian": "MW",
    "rwandan": "RW",
    "somali": "SO",
    "sudanese": "SD",
    "tunisian": "TN",
    "algerian": "DZ",
    "moroccan": "MA",
    "libyan": "LY",
    "ivorian": "CI",
    "malian": "ML",
    "nigerien": "NE",
    "burkinabe": "BF",
    "beninese": "BJ",
    "togolese": "TG",
    "guinean": "GN",
    "sierra leonean": "SL",
    "liberian": "LR",
    "gambian": "GM",
    "mauritanian": "MR",
    "chadian": "TD",
    "congolese": "CG",
    "malagasy": "MG",
    "botswanan": "BW",
    "namibian": "NA",
    "basotho": "LS",
    "swazi": "SZ",
    "eritrean": "ER",
    "djiboutian": "DJ",
    "burundian": "BI",
    "mauritian": "MU",
    "american": "US",
    "british": "GB",
    "french": "FR",
    "german": "DE",
    "indian": "IN",
    "chinese": "CN",
    "brazilian": "BR",
    "canadian": "CA",
    "australian": "AU",
    "portuguese": "PT",
    "spanish": "ES",
    "italian": "IT",
    "dutch": "NL",
    "swedish": "SE",
    "norwegian": "NO",
    "danish": "DK",
    "finnish": "FI",
    "polish": "PL",
    "russian": "RU",
    "turkish": "TR",
    "saudi": "SA",
    "pakistani": "PK",
    "bangladeshi": "BD",
    "indonesian": "ID",
    "filipino": "PH",
    "mexican": "MX",
    "argentinian": "AR",
    "colombian": "CO",
    "venezuelan": "VE",
    "peruvian": "PE",
    "chilean": "CL",
    "japanese": "JP",
    "korean": "KR",
    "vietnamese": "VN",
    "thai": "TH",
    "malaysian": "MY",
    "iranian": "IR",
    "iraqi": "IQ",
    "jordanian": "JO",
    "lebanese": "LB",
    "israeli": "IL",
    "greek": "GR",
    "ukrainian": "UA",
    "romanian": "RO",
    "hungarian": "HU",
    "slovak": "SK",
    "croatian": "HR",
    "serbian": "RS",
    "singaporean": "SG",
    "new zealander": "NZ",
}

# Words to ignore when parsing
STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "from", "in",
    "who", "are", "is", "be", "with", "people", "person",
    "men", "women", "man", "woman", "those", "all", "show",
    "me", "find", "get", "list", "give", "between", "aged",
    "age", "above", "below", "over", "under", "than",
}


def get_country_id(phrase: str) -> str | None:
    """
    Try to match a word or phrase to an ISO country code.
    Checks adjectives first, then uses pycountry for country names.
    """
    # Check adjectives map first
    if phrase in ADJECTIVES:
        return ADJECTIVES[phrase]

    # Skip stopwords and very short words
    if phrase in STOPWORDS or len(phrase) < 3:
        return None

    # Try direct alpha_2 code e.g. "ng", "ke"
    if len(phrase) == 2:
        result = pycountry.countries.get(alpha_2=phrase.upper())
        if result:
            return result.alpha_2

    # Try pycountry fuzzy search for country names e.g. "nigeria", "kenya"
    try:
        results = pycountry.countries.search_fuzzy(phrase)
        if results:
            return results[0].alpha_2
    except LookupError:
        pass

    return None


def parse_query(q: str) -> dict | None:
    """
    Parsing a plain English query into a filters dictionary.
    Returns None if the query cannot be interpreted.

    Examples here would be:
        "young males from nigeria"
        as {"gender": "male", "min_age": 16, "max_age": 24, "country_id": "NG"}

        "females above 30"
        as {"gender": "female", "min_age": 30}

        "adult males from kenya"
        as {"gender": "male", "age_group": "adult", "country_id": "KE"}

        "male and female teenagers above 17"
        as {"age_group": "teenager", "min_age": 17}
    """
    if not q or not q.strip():
        return None

    text = q.lower().strip()
    filters = {}

    # Gender
    words = text.split()

    if any(w in words for w in ["male", "males", "man", "men"]) and \
    not any(w in words for w in ["female", "females", "woman", "women"]):
        filters["gender"] = "male"
    elif any(w in words for w in ["female", "females", "woman", "women"]):
        filters["gender"] = "female"

    # handle "male and female" — no gender filter will be needed again if male or female is there
    if any(w in words for w in ["male", "males"]) and \
    any(w in words for w in ["female", "females"]):
        filters.pop("gender", None)


    # Age group
    # "young" maps to ages 16-24

    if "young" in text:
        filters["min_age"] = 16
        filters["max_age"] = 24
    elif "child" in text or "children" in text:
        filters["age_group"] = "child"
    elif any(w in text for w in ["teenager", "teenagers", "teen", "teens"]):
        filters["age_group"] = "teenager"
    elif "adult" in text or "adults" in text:
        filters["age_group"] = "adult"
    elif any(w in text for w in ["senior", "seniors", "elderly"]):
        filters["age_group"] = "senior"

 
    # Age Ranges
    # "above age X" / "over age X" / "older than age X" → min_age
    match = re.search(r"(?:above|over|older than)\s+(\d+)", text)
    if match:
        filters["min_age"] = int(match.group(1))

    # "below age X" / "under age X" / "younger than age age X" → max_age
    match = re.search(r"(?:below|under|younger than)\s+(\d+)", text)
    if match:
        filters["max_age"] = int(match.group(1))

    # "between age X and age Y" → min_age + max_age
    match = re.search(r"between\s+(\d+)\s+and\s+(\d+)", text)
    if match:
        filters["min_age"] = int(match.group(1))
        filters["max_age"] = int(match.group(2))



    # Country
    # Trying multi-word phrases first (longest first) then single words
    words = text.split()

    # Trying phrases from longest to shortest to catch "south africa" before "africa"
    for length in range(len(words), 0, -1):
        if "country_id" in filters:
            break
        for i in range(len(words) - length + 1):
            phrase = " ".join(words[i:i + length])
            if phrase in STOPWORDS:
                continue
            country_id = get_country_id(phrase)
            if country_id:
                filters["country_id"] = country_id
                break

    # Making sure that at least one meaningful filter must be extracted
    
    if not filters:
        return None

    return filters