package main

import (
	"log"
	"os"
	"strings"

	"github.com/pablopovar/spam-hop/transport/internal/transport"
)

func getenv(key string, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}

	return fallback
}

func required(key string) string {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		log.Fatalf("%s is required", key)
	}
	return value
}

func main() {
	cfg := transport.Config{
		Listen: getenv(
			"SPAMHOP_LISTEN",
			":1143",
		),

		Upstream: required("SPAMHOP_UPSTREAM"),

		UpstreamTLS: required("SPAMHOP_UPSTREAM_TLS"),

		UpstreamUsername: required("SPAMHOP_UPSTREAM_USERNAME"),
		UpstreamPassword: required("SPAMHOP_UPSTREAM_PASSWORD"),

		ClientUsername: required("SPAMHOP_CLIENT_USERNAME"),
		ClientPassword: required("SPAMHOP_CLIENT_PASSWORD"),

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
