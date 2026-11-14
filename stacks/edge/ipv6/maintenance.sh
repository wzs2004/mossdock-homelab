umask 077
/usr/bin/python3 ./ddns.py
mossdock_acme="$HOME/Library/MossdockIPv6"
/bin/sh "$mossdock_acme/acme.sh" --home "$mossdock_acme/acme-state" --cron
mossdock_cert_source="$mossdock_acme/acme-state/dav6.mossdock.autos_ecc"
if ! cmp -s "$mossdock_cert_source/fullchain.cer" ./certs/fullchain.cer; then
    cp "$mossdock_cert_source/dav6.mossdock.autos.key" ./certs/
    cp "$mossdock_cert_source/fullchain.cer" ./certs/
fi
mossdock_cert_digest=$(/usr/bin/shasum -a 256 ./certs/fullchain.cer)
if [[ "$mossdock_cert_digest" != "$(cat ./certs.loaded 2>/dev/null)" ]]; then
    "$HOME/.docker/bin/docker" exec mossdock-ipv6-https caddy reload --config /etc/caddy/Caddyfile --force &&
        printf '%s\n' "$mossdock_cert_digest" > ./certs.loaded
fi
