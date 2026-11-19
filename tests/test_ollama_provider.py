import unittest

from spamhop.provider.ollama import Provider


class OllamaProviderTests(unittest.TestCase):
    def test_root_base_url_builds_api_chat_endpoint(self):
        provider = Provider(
            base_url="http://localhost:11434",
            api_key="",
            model="example",
            timeout_seconds=1,
        )
        self.assertEqual(provider._chat_url(), "http://localhost:11434/api/chat")

    def test_api_base_url_builds_chat_endpoint(self):
        provider = Provider(
            base_url="http://localhost:11434/api",
            api_key="",
            model="example",
            timeout_seconds=1,
        )
        self.assertEqual(provider._chat_url(), "http://localhost:11434/api/chat")


if __name__ == "__main__":
    unittest.main()
