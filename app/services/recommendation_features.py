"""Quantity-independent features. Never infer food safety from an expiry date."""
from datetime import date
from math import isfinite
from app.services.recipes import ingredient_key, PARENTS


def fridge_features(recipe, foods, today: date, urgency_days: int = 7):
    if urgency_days < 1:
        raise ValueError('urgency_days must be positive')
    required = {p['key'] for p in recipe['ingredients']}
    owned, urgency = set(), {}
    for food in foods:
        expiry = food.expiry_date
        if expiry and expiry < today:
            continue  # Existing exclusion policy; not a safety diagnosis.
        key = ingredient_key(food.name)
        options = {key, PARENTS.get(key, key)}
        owned.update(options)
        score = max(0., 1 - (expiry - today).days / urgency_days) if expiry else 0.
        for option in options:
            urgency[option] = max(urgency.get(option, 0.), score)
    count = len(required)
    return {'ingredient_coverage': len(required & owned) / count if count else 0.,
            'expiry_priority': sum(urgency.get(k, 0.) for k in required) / count if count else 0.}


def rating_score(total: float, count: int, mean: float = 3., strength: float = 10.):
    if count < 0 or strength <= 0 or not all(isfinite(x) for x in [total,mean,strength]):
        raise ValueError('Invalid Bayesian rating inputs')
    return max(0., min(1., ((total + strength * mean) / (count + strength) - 1) / 4))
