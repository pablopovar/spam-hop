package transport

import (
	"github.com/emersion/go-imap/v2"
	"github.com/emersion/go-imap/v2/imapserver"
)

type Server struct {
	cfg       Config
	processor *ProcessorClient
	imap      *imapserver.Server
}

func NewServer(cfg Config) *Server {
	server := &Server{
		cfg:       cfg,
		processor: NewProcessorClient(cfg.ProcessorURL),
	}

	server.imap = imapserver.New(&imapserver.Options{
		NewSession: func(
			_ *imapserver.Conn,
		) (imapserver.Session, *imapserver.GreetingData, error) {
			return newSession(
				cfg,
				server.processor,
			), nil, nil
		},

		// VPN-only PoC.
		InsecureAuth: true,

		Caps: imap.CapSet{
			imap.CapIMAP4rev1: {},
		},
	})

	return server
}

func (s *Server) ListenAndServe() error {
	return s.imap.ListenAndServe(s.cfg.Listen)
}
