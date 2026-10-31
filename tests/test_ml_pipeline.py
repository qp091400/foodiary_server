"""Dependency-free preprocessing checks; no fabricated model metrics."""
import csv
import tempfile
import unittest
from pathlib import Path
from ml.pipeline import load_data, temporal_split

class PipelineTest(unittest.TestCase):
    def test_calendar_split_no_overlap(self):
        events = [{'date':f'2020-01-{i:02}', 'user':'u'} for i in range(1,21)]
        train, valid, test = temporal_split(events)
        self.assertLess(max(e['date'] for e in train),min(e['date'] for e in valid))
        self.assertLess(max(e['date'] for e in valid),min(e['date'] for e in test))
        self.assertEqual(sum(map(len,[train,valid,test])),20)

    def test_zero_and_unknown_are_not_negatives(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with (root/'RAW_recipes.csv').open('w') as f:
                writer = csv.writer(f); writer.writerow(['id','name','ingredients']); writer.writerow(['1','rice',"['rice']"])
            with (root/'RAW_interactions.csv').open('w') as f:
                writer = csv.writer(f); writer.writerow(['user_id','recipe_id','date','rating'])
                writer.writerows([['u','1','2020-01-01',0],['u','1','2020-01-02',1],['u','2','2020-01-03',5]])
            _, events = load_data(root)
            self.assertEqual([e['rating'] for e in events],[1])

if __name__ == '__main__': unittest.main()

class FeatureLeakageTest(unittest.TestCase):
    def test_same_day_and_target_exclusion(self):
        import numpy as np
        from ml.pipeline import features
        vectors = {'a':np.eye(32)[0], 'b':np.eye(32)[1]}
        events = [dict(user='u',recipe='a',date='2020-01-01',rating=5),
                  dict(user='u',recipe='b',date='2020-01-01',rating=1),
                  dict(user='u',recipe='a',date='2020-01-02',rating=1)]
        x, y, _, h, t = features(events,vectors)
        np.testing.assert_equal(x[:2,0], [0,0])
        self.assertEqual(x[2,0],0)  # prior target rating excluded
        self.assertEqual(t['a'],[1.,1])  # replacement, not duplicate vote
        original = dict(h['u'])
        features([dict(user='u',recipe='b',date='2020-01-03',rating=5)],vectors,h,t,update=False)
        self.assertEqual(h['u'],original)

class OptimizedHistoryTest(unittest.TestCase):
    def test_running_profiles_equal_reference_for_replacements_and_same_day(self):
        import numpy as np
        from ml.pipeline import features
        from ml.study_features import build
        rng=np.random.default_rng(42)
        vectors={str(i):rng.normal(size=32).astype('float32') for i in range(5)}
        vectors={r:v/np.linalg.norm(v) for r,v in vectors.items()}
        events=[]
        for day in range(1,12):
            for user in ['a','b','c']:
                events.append(dict(user=user,recipe=str(day%5),date=f'2020-01-{day:02}',rating=(day+len(user))%5+1))
        ref=features(events,vectors);fast=build(events,vectors)
        np.testing.assert_allclose(ref[0],fast[0],atol=1e-6)
        np.testing.assert_array_equal(ref[1],fast[1]);self.assertEqual(ref[3],fast[3]);self.assertEqual(ref[4],fast[4])

    def test_ranking_requires_twenty_unique_mixed_candidates(self):
        from ml.ranking_evaluation import evaluate,paired_bootstrap
        y=[1,0]*10;s=[.9,.1]*10;u=['u']*20;r=list(map(str,range(20)))
        metrics,rows=evaluate(y,s,u,r)
        self.assertEqual(metrics['eligible_users'],1);self.assertEqual(metrics['ndcg_at_10'],1)
        metrics,_=evaluate(y[:19],s[:19],u[:19],r[:19]);self.assertEqual(metrics['eligible_users'],0)
        metrics,_=evaluate(y,s,u,['same']*20);self.assertEqual(metrics['eligible_users'],0)
        comparison=paired_bootstrap(rows,rows)
        self.assertEqual(comparison['ci95'],[0.,0.])

class RepresentationTest(unittest.TestCase):
    def test_only_recipe_content_and_shared_projection_are_used(self):
        import numpy as np
        from ml.representation import recipe_texts,project,normalize
        recipe={'name':'계란말이','ingredients':[{'name':'계란','text':'계란 100g','quantity':100}]}
        self.assertEqual(recipe_texts(recipe),recipe_texts({**recipe,'rating':1,'expiry':'2000-01-01','user_id':999}))
        rng=np.random.default_rng(42);body=rng.normal(size=(2,384)).astype('float32');title=rng.normal(size=(2,384)).astype('float32')
        projection={'mean':np.zeros(384),'components':np.eye(384)[:32]}
        np.testing.assert_allclose(project(body,title,projection,{}),normalize(body[:,:32]))
        actual=project(body,title,projection,{'title_weight':.65})
        np.testing.assert_allclose(np.linalg.norm(actual,axis=1),1.)

class HistoryStateTest(unittest.TestCase):
    def test_state_matches_feature_builder_without_scores(self):
        import numpy as np
        from ml.study_features import build,history_state
        events=[dict(user='u',recipe='a',date='2020-01-01',rating=5),dict(user='u',recipe='a',date='2020-01-02',rating=1),dict(user='v',recipe='a',date='2020-01-02',rating=3)]
        _,_,_,h,t=build(events,{'a':np.eye(32)[0]})
        actual_h,actual_t=history_state(events)
        self.assertEqual(h,actual_h);self.assertEqual(t,actual_t)
