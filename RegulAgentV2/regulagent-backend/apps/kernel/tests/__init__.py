from django.test import TestCase
from django.db import connections
from django.db.utils import OperationalError

class VectorTestSetupTestCase(TestCase):

    databases = '__all__'

    def setUp(self):
        # Ensure the vector extension is created for the test database
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        except OperationalError as e:
            print("Could not create vector extension:", e)
