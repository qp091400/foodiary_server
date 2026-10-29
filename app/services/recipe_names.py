"""Short presentation names without rewriting source titles or recommendation data."""
import html
import re


def display_name(recipe):
    original = str(recipe.get('name') or '').strip()
    canonical = str(recipe.get('dish_name') or '').strip()
    if canonical.lower() in {'nan', 'none', 'null', '-'}:
        canonical = ''
    name = html.unescape(canonical or original)
    if not canonical:
        # Author/campaign labels and trailing how-to prose are not part of the dish.
        name = re.sub(r'^\s*(?:\[[^\]]*\]\s*)+', '', name)
        name = re.split(r'\s*(?:만드는\s*(?:법|방법)|만들기|황금\s*레시피|레시피)\b', name, maxsplit=1)[0]
        name = re.split(r'\s*(?:\(\s*feat\.?|[!！]|::)', name, maxsplit=1, flags=re.I)[0]
    name = re.sub(r'^(?:(?:초간단|초스피드|간단한|간편한|맛있는|간단하게|맛있게)\s*)+', '', name)
    name = re.sub(r'\s+', ' ', name).strip(' #·•~!★☆♥♡<>[]"\'')
    # A cleanup rule must never erase the only available title.
    return name or canonical or original or '이름 없는 요리'


def with_display_name(recipe):
    return {**recipe, 'display_name': display_name(recipe)} if recipe else None


def dish_family(recipe):
    """Group presentation-equivalent dishes, preserving meaningful ingredient modifiers."""
    name = re.sub(r'[^가-힣a-z0-9]', '', display_name(recipe).lower())
    name = name.replace('계란', '달걀').replace('쇠고기', '소고기').replace('무우국', '무국').replace('뭇국', '무국')
    name = name.replace('소세지', '소시지').replace('쭈꾸미', '주꾸미').replace('마늘종', '마늘쫑')
    name = re.sub(r'(?:만드는법|만드는방법|만들기|황금레시피|레시피)$', '', name)
    name = re.sub(r'^(?:(?:백종원|류수영|어남선생|김수미|수미네|알토란|집밥|초간단|간단한|'
                  r'매콤한|매콤|얼큰한|얼큰|맛있는|담백한|에어프라이어|전자레인지|전자렌지|원팬|오븐))+', '', name)
    if name in {'', '요리', '테스트요리'}:
        return 'record:' + str(recipe['id'])
    # Normalize ingredient word order only when the entire dish prefix is recognized.
    # Unknown words are retained, rather than using fuzzy matches that merge different dishes.
    tokens = sorted({'베이컨', '가래떡', '떡', '치즈', '두부', '김치', '참치', '달걀',
                     '토마토', '감자', '고구마', '양파', '오이', '단호박', '팽이버섯',
                     '버섯', '소고기', '돼지고기', '닭가슴살', '새우', '문어', '오징어'}, key=len, reverse=True)
    for ending in ('볶음밥', '샐러드', '찌개', '덮밥', '볶음', '무침', '말이', '구이', '전', '국', '탕'):
        if not name.endswith(ending):
            continue
        rest, ingredients = name[:-len(ending)], []
        while rest:
            token = next((t for t in tokens if rest.startswith(t)), None)
            if not token:
                break
            ingredients.append('떡' if token == '가래떡' else token)
            rest = rest[len(token):]
        if not rest and len(ingredients) >= 2:
            return 'combination:' + ending + ':' + '+'.join(sorted(ingredients))
        break
    return name
