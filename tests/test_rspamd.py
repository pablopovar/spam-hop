import unittest

from spamhop.technical.rspamd import RspamdClient


class RspamdMetadataTests(unittest.TestCase):
    def test_extracts_envelope_recipient_and_top_received_ip(self):
        message = (
            b"Return-Path: <sender@example.net>\r\n"
            b"X-Original-To: user@example.com\r\n"
            b"Received: from mail.example.net (mail.example.net [203.0.113.10]) "
            b"by mx.example.com with ESMTP\r\n"
            b"Received: from forged.example (forged.example [192.0.2.55]) "
            b"by mail.example.net\r\n"
            b"Subject: test\r\n\r\nbody"
        )

        metadata = RspamdClient._metadata(message)

        self.assertEqual(metadata["From"], "sender@example.net")
        self.assertEqual(metadata["Deliver-To"], "user@example.com")
        self.assertEqual(metadata["IP"], "203.0.113.10")

    def test_does_not_use_lower_received_ip(self):
        message = (
            b"Received: by local.example with ESMTP\r\n"
            b"Received: from forged.example (forged.example [192.0.2.55]) "
            b"by local.example\r\n"
            b"Subject: test\r\n\r\nbody"
        )

        metadata = RspamdClient._metadata(message)

        self.assertNotIn("IP", metadata)


if __name__ == "__main__":
    unittest.main()
