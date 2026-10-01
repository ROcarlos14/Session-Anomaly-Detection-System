FROM nimlang/nim:alpine

WORKDIR /app

COPY . .

# Build release version
RUN nim c -d:release orchestrator.nim

CMD ["./orchestrator"]
