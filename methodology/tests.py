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
        self.assertContains(response, "NER Skill Evidence")
        self.assertContains(response, "Data Export Views")
        self.assertContains(response, "End-to-End Pipeline")
        self.assertContains(response, "Pipeline + funnel")
        self.assertContains(response, "Ingest Evidence")
        self.assertContains(response, "Clean and Deduplicate")
        self.assertContains(response, "Comparable signals")
        self.assertContains(response, "Actionable outputs")
        self.assertContains(response, "Skill Forecasting")
        self.assertContains(response, "Semantic Skill Clustering")
        self.assertContains(response, "Cross-tab heatmap")
        self.assertContains(response, "The number inside each cell is a count of job adverts")
        self.assertContains(response, "MathJax")
        self.assertContains(response, "Adzuna deduplication")
