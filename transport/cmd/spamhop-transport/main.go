package main

import (
	"log"
	"os"

	"github.com/pablopovar/spam-proxy/transport/internal/transport"
)

func getenv(key string, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}

	return fallback
}

func main() {
	cfg := transport.Config{
		Listen: getenv(
			"SPAMHOP_LISTEN",
			":1143",
		),

		Upstream: getenv(
			"SPAMHOP_UPSTREAM",
			"imap.example.invalid:993",
		),

		UpstreamTLS: getenv(
			"SPAMHOP_UPSTREAM_TLS",
			"implicit",
		),

		ProcessorURL: getenv(
			"SPAMHOP_PROCESSOR_URL",
			"http://127.0.0.1:18080",
		),
	}

	log.Printf(
		"SpamHop transport %s -> %s",
		cfg.Listen,
		cfg.Upstream,
	)

	if err := transport.NewServer(
		cfg,
	).ListenAndServe(); err != nil {
		log.Fatal(err)
	}
}
