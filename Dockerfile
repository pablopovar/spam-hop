FROM golang:1.26.5-alpine AS transport-build

WORKDIR /src

COPY transport/go.mod transport/go.sum ./
RUN go mod download

COPY transport/ ./

RUN CGO_ENABLED=0 go build \
    -trimpath \
    -ldflags="-s -w" \
    -o /out/spamhop-transport \
    ./cmd/spamhop-transport


FROM python:3.14.6-alpine

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY spamhop ./spamhop

COPY --from=transport-build \
    /out/spamhop-transport \
    /usr/local/bin/spamhop-transport

EXPOSE 1143

ENTRYPOINT ["python", "-m", "spamhop"]
