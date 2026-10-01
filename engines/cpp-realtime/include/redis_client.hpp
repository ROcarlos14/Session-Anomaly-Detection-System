#pragma once
#include <string>
#include <vector>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <cstring>
#include <netdb.h>

class RedisClient {
public:
    RedisClient() : sock(-1) {
    }
    
    ~RedisClient() {
        if (sock != -1) close(sock);
    }

    bool connect_redis(const std::string& host, int port) {
        sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock < 0) return false;

        struct hostent *he = gethostbyname(host.c_str());
        if (he == nullptr) return false;

        sockaddr_in addr;
        addr.sin_family = AF_INET;
        addr.sin_port = htons(port);
        addr.sin_addr = *((struct in_addr **)he->h_addr_list)[0];

        return connect(sock, (struct sockaddr*)&addr, sizeof(addr)) >= 0;
    }

    std::string execute_simple(const std::string& cmd) {
        send(sock, cmd.c_str(), cmd.length(), 0);
        char buffer[4096] = {0};
        int bytes = recv(sock, buffer, sizeof(buffer) - 1, 0);
        if (bytes > 0) return std::string(buffer, bytes);
        return "";
    }

    std::string xadd(const std::string& stream, const std::string& id, const std::vector<std::pair<std::string, std::string>>& fields) {
        // Simplified RESP array builder for XADD
        // *<count>\r\n$4\r\nXADD\r\n$<len>\r\n<stream>\r\n$<len>\r\n<id>...
        int count = 3 + (fields.size() * 2);
        std::string cmd = "*" + std::to_string(count) + "\r\n";
        cmd += "$4\r\nXADD\r\n";
        cmd += "$" + std::to_string(stream.length()) + "\r\n" + stream + "\r\n";
        cmd += "$" + std::to_string(id.length()) + "\r\n" + id + "\r\n";
        for (const auto& f : fields) {
            cmd += "$" + std::to_string(f.first.length()) + "\r\n" + f.first + "\r\n";
            cmd += "$" + std::to_string(f.second.length()) + "\r\n" + f.second + "\r\n";
        }
        return execute_simple(cmd);
    }

private:
    int sock;
};
