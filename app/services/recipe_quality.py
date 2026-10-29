"""Meal-focused screening with explicit review reasons; source records remain intact."""
import hashlib
import json
import re
from collections import Counter

from app.services.recipes import STAPLES, SUPPLIES, combination_keys, compact
from app.services.recipe_names import dish_family

QUALITY_VERSION = 4
PREPARATION_COOKING = re.compile(r'삶는|삶기|삶은법|삶은방법|삶아|굽는|굽기|찌는법|찌는방법|찌기|데치는|데치기')
COMBINED_DISH = re.compile(
    r'볶음|무침|찌개|전골|조림|샐러드|초회|김밥|초밥|덮밥|비빔밥|주먹밥|쌈밥|'
    r'국수|파스타|스파게티|수제비|리조또|샌드위치|토스트|피자|잡채|말이|'
    r'스튜|카레|오믈렛|계란찜|달걀찜|찜닭|갈비찜|해물찜|아귀찜|'
    r'쿠키|케이크|케익|머핀|빵|푸딩|스무디|에이드|주스|쥬스|라떼|'
    r'(?:국|탕|전|죽|밥|스프|수프)$')
PREPARATION_AIDS = {'무', '무우', '양파', '대파', '파', '마늘', '생강', '밀가루', '녹차',
                    '다시마', '레몬', '식초', '청주', '소주', '맛술'}
TIPS = re.compile(
    r'손질|세척|해동|보관|전처리|소독|살균|해감|씻는|씻기|씻어|헹구|헹굼|'
    r'까는|까기|깎는|깎기|깍는|깍기|자르는|자르기|썰기|써는|채치는|채치기|다지는|다지기|'
    r'껍질벗|껍질제거|씨빼|씨제거|가시제거|비린내제거|물기제거|핏물빼|핏물제거|'
    r'불리는법|불리는방법|불리기|데치는법|데치는방법|데치기|데치고|'
    r'모양내|모양만들|꽃모양|장식|플레이팅|데코|칼집|묶는법|'
    r'고르는|고르기|구별법|구별방법|구분법|효능|영양정보|먹는법|먹는방법|먹는순서|먹는시간|'
    r'사용법|사용방법|관리법|청소|재활용|활용팁|생활정보|생활꿀팁|식생활정보|'
    r'벗기는|벗기기|껍질까|껍질깎|껍질제거|껍질분리|껍질쉽게|'
    r'손쉽게까|쉽게까|잘까|쉽게자르|잘자르|컷팅|커팅|채썰|얇게썰|'
    r'씨앗발아|수확하기|재배|키우기|심는법|심기|거품내기|반죽법|반죽하는|'
    r'알빼|알맹이빼|알빼내|빼내는|껍질벗겨|꽃접기|접는법|접기|'
    r'잘라|썰어|깎아|벗겨|찢는법|찢기|다져|소분|갈무리|뿌리제거|꼭지제거|'
    r'가르는법|가르기|잎떼|떼는법|비늘제거|모래빼|노른자분리|흰자분리|데침|'
    r'말리는법|말리기|건조법|건조하기|성에제거|기름제거|기름걸러|냉동하는|얼리는법|얼리기')
# Require an identifiable finished dish rather than a generic verb such as '만들기'.
DISH = re.compile(
    r'볶음|무침|찌개|전골|조림|구이|찜|튀김|커틀릿|돈까스|돈가스|탕수육|수육|숙회|'
    r'김밥|초밥|주먹밥|덮밥|비빔밥|볶음밥|쌈밥|잡채|국수|라면|냉면|우동|파스타|스파게티|'
    r'샐러드|샌드위치|토스트|햄버거|피자|그라탕|리조또|스튜|카레|오믈렛|계란말이|달걀말이|'
    r'쿠키|케이크|케익|머핀|브라우니|마들렌|스콘|베이글|빵|도넛|와플|팬케이크|파이|타르트|'
    r'푸딩|젤리|아이스크림|빙수|초콜릿|잼|청만들기|장아찌|피클|김치|겉절이|나물|'
    r'스무디|셰이크|쉐이크|라떼|주스|쥬스|에이드|칵테일|요거트|요구르트|밀크티|'
    r'떡볶이|떡국|떡꼬치|떡갈비|경단|인절미|송편|약과|양갱|부침개|전병|호떡|'
    r'감바스|라자냐|불고기|떡|소스|드레싱|양념장|쌈장|쨈|후리가케|오트밀|누룽지|'
    r'마카롱|카라멜|캐러멜|카스테라|치킨|스테이크|커리|퐁듀|퐁당|몬테크리스토|'
    r'팝콘|부각|다짐장|생채|오니기리|핫도그|밥도그|말랭이|카나페|치즈볼|'
    r'가스|깐풍|유린기|난자완스|가라아게|짜파구리|짜장|짬뽕|식혜|수정과|화채|펀치|'
    r'백숙|삼계탕|보쌈|통닭|강정|맛탕|빠스|꼬치|또띠아롤|라이스페이퍼말이|'
    r'후라이|프라이|스크램블|수란|에그|프리타타|낙곱새|밀푀유|순대|절임|라페|'
    r'크로켓|고로케|타코|케밥|브리또|부리토|하이라이스|브런치|두루치기|쫄면|'
    r'소박이|오이지|피클|살사|리코타|라코타|포케|월남쌈|우엉쌈|달걀쌈|'
    r'과카몰리|과콰몰리|후무스|타불레|퀘사디아|퀘사디야|함박|주물럭|쫀드기|'
    r'육회|회무침|물회|회덮밥|붕어빵|단팥|아란치니|뇨끼|뇨키|로제|팬나코타|판나코타|'
    r'소바|냉채|편강|크림치즈|콩국물|국물|짜글이|치즈스틱|소금빵|계란장|달걀장|'
    r'스프레드|져키|저키|꼬지|골든자|부르기뇽|크레이프|크레페|약밥|오코노미야키|오코노미야끼')
COOKING = re.compile(r'삶는|삶기|삶은|굽는|굽기|구운|찌는|찐|요리|레시피|만드는|만들기')
COLLECTION = re.compile(r'모음|모아보기|식단표|주간식단|일주일식단|한주식단|한상차림|'
                        r'(?:일주일|한주|주간).{0,12}(?:밑반찬|반찬)|반찬[3-9]가지|[3-9]가지반찬')
NON_RECIPE = re.compile(r'언박싱|먹방|제품리뷰|상품리뷰|구매후기|구입후기|제품후기|체험후기|'
                        r'맛집방문|맛집추천|식당추천|이벤트안내|공동구매|공구안내')
NON_FOOD = re.compile(r'천연세제|세탁세제|주방세제|천연가습기|탈취제|소독제|화분|식탁세팅|'
                      r'센터피스|쎈터피스|장난감|설거지|수세미세척|수세미소독|비누만들|천연비누|'
                      r'행주삶|행주빨|행주소독|냅킨|냅킨아트|비닐정리')
PET_FOOD = re.compile(r'강아지|고양이|반려견|반려묘|애견|애묘|펫푸드|애완견|노견')


def is_dish(name):
    return bool(DISH.search(name) or re.search(
        r'(?:국|탕|전|죽|밥|차|술|즙|묵|쌈|롤|회|칩|청|수제비)(?:만들|레시피|끓|요리|맛있|$)', name))


def classify(recipe):
    def normalized_name(value):
        return compact(value).replace('계란', '달걀').replace('쭈꾸미', '주꾸미').replace('마늘종', '마늘쫑')
    title = normalized_name(recipe['name'])
    dish = normalized_name(recipe.get('dish_name') or '')
    keys = {part['key'] for part in recipe['ingredients']}
    if NON_FOOD.search(title) and not (dish and is_dish(dish) and dish in title):
        return '음식이 아닌 생활용품·생활정보'
    if PET_FOOD.search(title) and not re.search(r'모양|캐릭터|동물틀', title):
        return '반려동물용 음식'
    if not keys or not keys - STAPLES - SUPPLIES:
        return '주재료 확인 불가'
    if len(combination_keys(recipe)) < 2:
        return '단독 재료 레시피'
    if re.search(r'숙회$|구이$|^군고구마$|^찐감자$|^찐고구마$', dish):
        foods = combination_keys(recipe)
        primary = {k for k in foods if len(k) >= 2 and k in dish}
        if primary and not (foods - primary - PREPARATION_AIDS):
            return '삶기·굽기·찌기 등 단순 조리 안내'
    if PREPARATION_COOKING.search(title) or PREPARATION_COOKING.search(dish):
        # Aromatics and cleaning flour do not turn "문어 삶는 법" into a composite dish.
        # A named resulting meal (e.g. 문어샐러드) must actually occur in the title.
        if not (dish and not PREPARATION_COOKING.search(dish)
                and COMBINED_DISH.search(dish) and dish in title):
            return '삶기·굽기·찌기 등 단순 조리 안내'
    collection_title = re.sub(r'\s+', '', recipe['name'])
    if COLLECTION.search(collection_title):
        if ('모음' in title or re.search(r'[3-9]가지|[3-9]종', collection_title)
                or not (dish and is_dish(dish) and dish in title)):
            return '모음·식단 등 여러 요리 혼합'
    # Canonical dish names can themselves be mislabeled (e.g. 메추리알장조림 on a peeling tip).
    # For tip-like titles require that the actual dish name occurs in the title as a dish.
    if TIPS.search(title) or TIPS.search(dish):
        if re.search(r'(?:재료|고명)(?:손질|준비)|(?:손질|세척|보관|해동|까기|자르기|썰기)용', title):
            return '손질·보관·장식 중심 또는 요리 확인 필요'
        if dish and re.search(re.escape(dish) + r'(?:보관|손질|세척|해동|까는|자르는|씻는)', title):
            # Bare ingredient/dish + handling instructions is not a cooking recipe.
            if not re.search(r'만들|만든|끓|볶|조림|무침|구이|튀김|레시피|담그|담는', title):
                return '손질·보관·장식 중심 또는 요리 확인 필요'
        if (dish and not TIPS.search(dish) and is_dish(dish) and dish in title
                and not re.search(re.escape(dish) + r'용', title)):
            return ''
        return '손질·보관·장식 중심 또는 요리 확인 필요'
    if NON_RECIPE.search(title):
        if not (dish and is_dish(dish) and dish in title and len(keys - STAPLES) >= 2):
            return '제품·체험·소개 정보'
    # Public sources have full steps. CSV entries with weak meal evidence are held out,
    # especially raw fruit/ingredient-only and generic miscellaneous entries.
    if recipe.get('source_key') == '10000recipe':
        if dish in {'요리', '음식', '메뉴', '반찬'}:
            return '완성 요리 근거 부족'
        if not dish and not is_dish(title):
            return '요리명 누락·확인 필요'
        if not is_dish(dish) and not is_dish(title):
            category = recipe.get('category', '')
            # A specific dish category plus multiple edible ingredients is supporting
            # evidence, but raw fruit and miscellaneous labels alone are insufficient.
            if len(keys - STAPLES - SUPPLIES) >= 2 and category not in ('기타', ''):
                return ''
            if category in ('기타', '') or not COOKING.search(title):
                if re.search(r'삶|굽|구운|찌는|찐', title):
                    return ''
                return '완성 요리 근거 부족'
    return ''


def group_key(recipe):
    dish = compact(recipe.get('dish_name') or recipe['name'])
    dish = dish.replace('계란', '달걀').replace('소세지', '소시지').replace('케찹', '케첩')
    keys = sorted({p['key'] for p in recipe['ingredients']})
    # Only the same dish, full ingredient combination and cooking method are grouped.
    # Different dishes sharing ingredients must not disappear.
    data = [dish, keys, compact(recipe.get('method', ''))]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def screen(db, overrides=None):
    overrides = overrides or {}
    if 'combination_count' not in {r[1] for r in db.execute('PRAGMA table_info(recipes)')}:
        db.execute('ALTER TABLE recipes ADD COLUMN combination_count INTEGER DEFAULT 0')
    if 'dish_family' not in {r[1] for r in db.execute('PRAGMA table_info(recipes)')}:
        db.execute('ALTER TABLE recipes ADD COLUMN dish_family TEXT')
    for rid, payload in db.execute('SELECT id,payload FROM recipes'):
        recipe = json.loads(payload)
        combination_count = len(combination_keys(recipe))
        reason = classify(recipe)
        group = group_key(recipe)
        override = overrides.get(rid)
        if override == 'include' and combination_count >= 2:
            reason, group = '', rid
        elif override == 'exclude':
            reason = '검토 후 제외'
        db.execute('UPDATE recipes SET recommendable=?,exclusion_reason=?,group_key=?,combination_count=?,dish_family=? WHERE id=?',
                   (not reason, reason, group, combination_count, dish_family(recipe), rid))
    db.execute('''UPDATE recipes SET recommendable=0, exclusion_reason='동일 요리·재료·조리법 중복'
                  WHERE id IN (
                    SELECT id FROM (
                      SELECT id,ROW_NUMBER() OVER (
                        PARTITION BY group_key ORDER BY has_steps DESC,id ASC
                      ) AS position FROM recipes WHERE recommendable=1
                    ) WHERE position>1
                  )''')
    counts = Counter({r[0]: r[1] for r in db.execute(
        'SELECT exclusion_reason,COUNT(*) FROM recipes WHERE recommendable=0 GROUP BY exclusion_reason')})
    return dict(counts)
