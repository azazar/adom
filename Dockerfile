FROM debian:9.4-slim

RUN echo "deb http://archive.debian.org/debian/ stretch main contrib non-free"                   > /etc/apt/sources.list \
 && echo "deb http://archive.debian.org/debian/ stretch-proposed-updates main contrib non-free" >> /etc/apt/sources.list \
 && echo "deb http://archive.debian.org/debian-security stretch/updates main contrib non-free"  >> /etc/apt/sources.list

ENV TERM xterm-256color

RUN useradd -m adom
WORKDIR /home/adom
VOLUME /home/adom/.adom.data

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        libncurses5 \
        tar \
    && curl -sSk https://www.adom.de/home/download/current/adom_linux_debian_64_3.3.3.tar.gz \
        | tar -xz --strip-components 1 \
    && savedPackages="libncurses5" \
    && apt-mark auto '.*' > /dev/null \
    && apt-mark manual $savedPackages \
    && apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false \
    && rm -rf /var/cache/apt/* /var/lib/apt/lists/*

USER adom

ENTRYPOINT ["./adom"]
