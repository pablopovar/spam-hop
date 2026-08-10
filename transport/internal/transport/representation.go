package transport

// BuildRepresentation is the transport-owned boundary between a score and the
// message representation exposed downstream. Header/metadata encoding is
// intentionally deferred while the transport architecture is being proven.
func BuildRepresentation(message []byte, _ ScoreResult) []byte {
	return message
}
