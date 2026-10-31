import unittest
from app.v3.qualification import matching_format_checks


class QualificationTests(unittest.TestCase):
    def test_format_proof_is_bound_to_endpoint_collection_image_and_model(self):
        context={'provider':'weknora','endpoint':'http://127.0.0.1:18080','collection_id':'kb-a','image_digest':'sha256:a','embedding_model':'embedding-a'}
        row={**context,'extension':'.pdf','status':'passed','fact_preserved':True}
        self.assertEqual(matching_format_checks([row],**context),[row])
        for field in context:
            with self.subTest(field=field):
                self.assertEqual(matching_format_checks([{**row,field:'different'}],**context),[])
        self.assertEqual(matching_format_checks([{'provider':'weknora','extension':'.pdf','status':'passed','fact_preserved':True}],**context),[])
