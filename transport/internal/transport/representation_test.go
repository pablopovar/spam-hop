package transport

import (
	"strings"
	"testing"
)

func TestBuildRepresentationAddsSpamHopHeaders(t *testing.T) {
	scoreValue := 1.0
	message := []byte("Subject: SpamHop Positive Test\r\nFrom: test@example.com\r\n\r\nbody")

	got := string(BuildRepresentation(message, ScoreResult{
		Score:   &scoreValue,
		Verdict: "spam",
		Engine:  "positive-test",
	}))

	for _, want := range []string{
		"X-SpamHop-Score: 1.0000\r\n",
		"X-SpamHop-Verdict: spam\r\n",
		"X-SpamHop-Engine: positive-test\r\n",
		"\r\n\r\nbody",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("representation missing %q: %q", want, got)
		}
	}
}

func TestBuildRepresentationSanitizesHeaderValues(t *testing.T) {
	message := []byte("Subject: test\n\nbody")
	got := string(BuildRepresentation(message, ScoreResult{
		Verdict: "spam\r\nInjected: yes",
	}))

	if strings.Contains(got, "\nInjected:") {
		t.Fatalf("header injection was not sanitized: %q", got)
	}
}
