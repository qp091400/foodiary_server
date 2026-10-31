"""Versioned recipe text construction and projection, shared by training/export."""
import html
import re
import unicodedata
import numpy as np

ENCODER='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
REVISION='e8f8c211226b894fcb81acc59f3b34ba3efd5f42'

def clean(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',html.unescape(str(text)))).strip()


def recipe_texts(recipe):
    ingredients=recipe['ingredients']
    names=[p['name'] if isinstance(p,dict) else p for p in ingredients]
    # Baseline body exactly matches V1; candidate title cleans presentation prose.
    body=recipe['name']+' | '+', '.join(sorted(set(names)))
    title=clean(recipe.get('dish_name') or recipe.get('display_name') or recipe['name'])
    title=re.sub(r'^(?:\[[^\]]*\]\s*)+','',title)
    return body,title


def normalize(values):
    return values/np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-8)


def combine(body,title,config):
    weight=config.get('title_weight',0.)
    if not weight: return body
    return normalize(weight*normalize(title)+(1-weight)*normalize(body))


def project(body,title,projection,config):
    raw=combine(body,title,config)
    return normalize((raw-projection['mean'])@projection['components'].T)


def encode_views(encoder,recipes,config,batch_size=128):
    """Use the identical prefix/token limit for offline export and new recipes."""
    if config.get('max_seq_length'):encoder.max_seq_length=config['max_seq_length']
    texts=[recipe_texts(r) for r in recipes];prefix=config.get('input_prefix','')
    body=encoder.encode([prefix+t[0] for t in texts],batch_size=batch_size,show_progress_bar=False)
    title=encoder.encode([prefix+t[1] for t in texts],batch_size=batch_size,show_progress_bar=False) if config.get('title_weight',0) else body
    return body,title
