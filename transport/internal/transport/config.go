package transport

type Config struct {
	Listen       string
	Upstream     string
	UpstreamTLS  string
	ProcessorURL string
}
