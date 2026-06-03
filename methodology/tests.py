from django.test import TestCase
from django.urls import reverse


class MethodologyPageTests(TestCase):
    def test_methodology_page_renders(self):
        response = self.client.get(reverse("methodology"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Normalized Embeddings")
        self.assertContains(response, "Cosine similarity")
        self.assertContains(response, "\\cos(A, B)")
        self.assertContains(response, "\\operatorname{final}")
        self.assertContains(response, "School Skill Matrix Calculations")
        self.assertContains(response, "Demand evidence")
        self.assertContains(response, "MathJax")
        self.assertContains(response, "Adzuna deduplication")
