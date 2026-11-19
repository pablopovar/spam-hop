package transport

type Config struct {
	Listen           string
	Upstream         string
	UpstreamTLS      string
	UpstreamUsername string
	UpstreamPassword string
	ClientUsername   string
	ClientPassword   string
	ProcessorURL     string
}
