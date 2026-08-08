package transport

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"

	"github.com/emersion/go-imap/v2"
	"github.com/emersion/go-imap/v2/imapclient"
	"github.com/emersion/go-imap/v2/imapserver"
)

type session struct {
	cfg       Config
	processor *ProcessorClient
	upstream  *imapclient.Client

	selected    bool
	numMessages uint32
}

func newSession(
	cfg Config,
	processor *ProcessorClient,
) *session {
	return &session{
		cfg:       cfg,
		processor: processor,
	}
}

func (s *session) dial() (*imapclient.Client, error) {
	switch s.cfg.UpstreamTLS {
	case "", "implicit":
		return imapclient.DialTLS(
			s.cfg.Upstream,
			nil,
		)

	case "starttls":
		return imapclient.DialStartTLS(
			s.cfg.Upstream,
			nil,
		)

	case "none":
		return imapclient.DialInsecure(
			s.cfg.Upstream,
			nil,
		)

	default:
		return nil, fmt.Errorf(
			"unsupported upstream TLS mode %q",
			s.cfg.UpstreamTLS,
		)
	}
}

func (s *session) requireUpstream() (*imapclient.Client, error) {
	if s.upstream == nil {
		return nil, errors.New("not authenticated upstream")
	}

	return s.upstream, nil
}

func (s *session) Close() error {
	if s.upstream == nil {
		return nil
	}

	err := s.upstream.Close()
	s.upstream = nil

	return err
}

func (s *session) Login(
	username string,
	password string,
) error {
	if s.upstream != nil {
		_ = s.upstream.Close()
		s.upstream = nil
	}

	client, err := s.dial()
	if err != nil {
		return err
	}

	if err := client.Login(
		username,
		password,
	).Wait(); err != nil {
		_ = client.Close()
		return imapserver.ErrAuthFailed
	}

	s.upstream = client

	return nil
}

func (s *session) Select(
	mailbox string,
	options *imap.SelectOptions,
) (*imap.SelectData, error) {
	client, err := s.requireUpstream()
	if err != nil {
		return nil, err
	}

	data, err := client.Select(
		mailbox,
		options,
	).Wait()
	if err != nil {
		return nil, err
	}

	s.selected = true
	s.numMessages = data.NumMessages

	return data, nil
}

func (s *session) Create(
	mailbox string,
	options *imap.CreateOptions,
) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	return client.Create(
		mailbox,
		options,
	).Wait()
}

func (s *session) Delete(mailbox string) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	return client.Delete(mailbox).Wait()
}

func (s *session) Rename(
	mailbox string,
	newName string,
	options *imap.RenameOptions,
) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	return client.Rename(
		mailbox,
		newName,
		options,
	).Wait()
}

func (s *session) Subscribe(mailbox string) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	return client.Subscribe(mailbox).Wait()
}

func (s *session) Unsubscribe(mailbox string) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	return client.Unsubscribe(mailbox).Wait()
}

func (s *session) List(
	writer *imapserver.ListWriter,
	reference string,
	patterns []string,
	options *imap.ListOptions,
) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	for _, pattern := range patterns {
		command := client.List(
			reference,
			pattern,
			options,
		)

		for {
			data := command.Next()
			if data == nil {
				break
			}

			if err := writer.WriteList(data); err != nil {
				_ = command.Close()
				return err
			}
		}

		if err := command.Close(); err != nil {
			return err
		}
	}

	return nil
}

func (s *session) Status(
	mailbox string,
	options *imap.StatusOptions,
) (*imap.StatusData, error) {
	client, err := s.requireUpstream()
	if err != nil {
		return nil, err
	}

	return client.Status(
		mailbox,
		options,
	).Wait()
}

func (s *session) Append(
	mailbox string,
	reader imap.LiteralReader,
	options *imap.AppendOptions,
) (*imap.AppendData, error) {
	client, err := s.requireUpstream()
	if err != nil {
		return nil, err
	}

	command := client.Append(
		mailbox,
		reader.Size(),
		options,
	)

	if _, err := io.Copy(
		command,
		reader,
	); err != nil {
		return nil, err
	}

	if err := command.Close(); err != nil {
		return nil, err
	}

	return command.Wait()
}

func (s *session) Poll(
	writer *imapserver.UpdateWriter,
	_ bool,
) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	if err := client.Noop().Wait(); err != nil {
		return err
	}

	if !s.selected {
		return nil
	}

	mailbox := client.Mailbox()
	if mailbox == nil {
		return nil
	}

	if mailbox.NumMessages != s.numMessages {
		s.numMessages = mailbox.NumMessages
		return writer.WriteNumMessages(
			mailbox.NumMessages,
		)
	}

	return nil
}

func (s *session) Idle(
	_ *imapserver.UpdateWriter,
	stop <-chan struct{},
) error {
	<-stop
	return nil
}

func (s *session) Unselect() error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	if err := client.Unselect().Wait(); err != nil {
		return err
	}

	s.selected = false
	s.numMessages = 0

	return nil
}

func (s *session) Expunge(
	writer *imapserver.ExpungeWriter,
	uids *imap.UIDSet,
) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	var command *imapclient.ExpungeCommand

	if uids != nil {
		command = client.UIDExpunge(*uids)
	} else {
		command = client.Expunge()
	}

	for {
		sequence := command.Next()
		if sequence == 0 {
			break
		}

		if err := writer.WriteExpunge(
			sequence,
		); err != nil {
			_ = command.Close()
			return err
		}
	}

	return command.Close()
}

func (s *session) Search(
	kind imapserver.NumKind,
	criteria *imap.SearchCriteria,
	options *imap.SearchOptions,
) (*imap.SearchData, error) {
	client, err := s.requireUpstream()
	if err != nil {
		return nil, err
	}

	if kind == imapserver.NumKindUID {
		return client.UIDSearch(
			criteria,
			options,
		).Wait()
	}

	return client.Search(
		criteria,
		options,
	).Wait()
}

func (s *session) Fetch(
	writer *imapserver.FetchWriter,
	numSet imap.NumSet,
	options *imap.FetchOptions,
) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	if options == nil {
		options = &imap.FetchOptions{}
	}

	if len(options.BinarySection) > 0 ||
		len(options.BinarySectionSize) > 0 ||
		options.ModSeq ||
		options.ChangedSince != 0 {
		return errors.New(
			"FETCH extension unsupported by PoC",
		)
	}

	needsRepresentation :=
		len(options.BodySection) > 0 ||
			options.RFC822Size ||
			options.BodyStructure != nil

	upstreamOptions := *options

	var wholeMessage *imap.FetchItemBodySection

	if needsRepresentation {
		peek := true

		for _, section := range options.BodySection {
			if !section.Peek {
				peek = false
				break
			}
		}

		wholeMessage = &imap.FetchItemBodySection{
			Peek: peek,
		}

		upstreamOptions.BodySection =
			[]*imap.FetchItemBodySection{
				wholeMessage,
			}

		upstreamOptions.RFC822Size = false
		upstreamOptions.BodyStructure = nil
	}

	command := client.Fetch(
		numSet,
		&upstreamOptions,
	)

	for {
		message := command.Next()
		if message == nil {
			break
		}

		buffer, err := message.Collect()
		if err != nil {
			_ = command.Close()
			return err
		}

		var representation []byte

		if needsRepresentation {
			original :=
				buffer.FindBodySection(
					wholeMessage,
				)

			if original == nil {
				_ = command.Close()

				return errors.New(
					"upstream did not return complete message",
				)
			}

			representation, err =
				s.processor.Process(
					context.Background(),
					original,
				)

			if err != nil {
				_ = command.Close()

				return fmt.Errorf(
					"processor failed: %w",
					err,
				)
			}
		}

		out := writer.CreateMessage(
			buffer.SeqNum,
		)

		if options.Flags {
			out.WriteFlags(buffer.Flags)
		}

		if options.Envelope {
			out.WriteEnvelope(buffer.Envelope)
		}

		if options.InternalDate {
			out.WriteInternalDate(
				buffer.InternalDate,
			)
		}

		if options.UID {
			out.WriteUID(buffer.UID)
		}

		if options.RFC822Size {
			out.WriteRFC822Size(
				int64(len(representation)),
			)
		}

		if options.BodyStructure != nil {
			bodyStructure :=
				imapserver.ExtractBodyStructure(
					bytes.NewReader(
						representation,
					),
				)

			out.WriteBodyStructure(
				bodyStructure,
			)
		}

		for _, section :=
			range options.BodySection {
			data :=
				imapserver.ExtractBodySection(
					bytes.NewReader(
						representation,
					),
					section,
				)

			bodyWriter :=
				out.WriteBodySection(
					section,
					int64(len(data)),
				)

			if _, err :=
				bodyWriter.Write(
					data,
				); err != nil {
				_ = bodyWriter.Close()
				_ = out.Close()
				_ = command.Close()

				return err
			}

			if err :=
				bodyWriter.Close(); err != nil {
				_ = out.Close()
				_ = command.Close()

				return err
			}
		}

		if err := out.Close(); err != nil {
			_ = command.Close()
			return err
		}
	}

	return command.Close()
}

func (s *session) Store(
	writer *imapserver.FetchWriter,
	numSet imap.NumSet,
	flags *imap.StoreFlags,
	options *imap.StoreOptions,
) error {
	client, err := s.requireUpstream()
	if err != nil {
		return err
	}

	command := client.Store(
		numSet,
		flags,
		options,
	)

	for {
		message := command.Next()
		if message == nil {
			break
		}

		buffer, err := message.Collect()
		if err != nil {
			_ = command.Close()
			return err
		}

		out := writer.CreateMessage(
			buffer.SeqNum,
		)

		if buffer.Flags != nil {
			out.WriteFlags(buffer.Flags)
		}

		if buffer.UID != 0 {
			out.WriteUID(buffer.UID)
		}

		if err := out.Close(); err != nil {
			_ = command.Close()
			return err
		}
	}

	return command.Close()
}

func (s *session) Copy(
	numSet imap.NumSet,
	destination string,
) (*imap.CopyData, error) {
	client, err := s.requireUpstream()
	if err != nil {
		return nil, err
	}

	return client.Copy(
		numSet,
		destination,
	).Wait()
}
