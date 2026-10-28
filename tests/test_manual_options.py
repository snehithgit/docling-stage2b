import unittest
from pydantic import ValidationError
from app.manual_options import ManualConvertOptions

class TestManualConvertOptions(unittest.TestCase):
    def test_empty_to_formats_raises_error(self):
        with self.assertRaises(ValidationError) as context:
            ManualConvertOptions(to_formats=[])

        self.assertIn("to_formats must be one or more of:", str(context.exception))

if __name__ == "__main__":
    unittest.main()
