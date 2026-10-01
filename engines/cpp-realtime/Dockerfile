FROM alpine:latest

RUN apk add --no-cache build-base

WORKDIR /app
COPY . .

# Adjust Makefile to not use -lws2_32 on Linux
RUN sed -i 's/-lws2_32//g' Makefile

RUN make

CMD ["./anomaly_cpp_engine.exe"]
