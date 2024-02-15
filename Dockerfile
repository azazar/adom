FROM debian:9.4-slim

RUN echo "deb http://archive.debian.org/debian/ stretch main contrib non-free"                   > /etc/apt/sources.list \
 && echo "deb http://archive.debian.org/debian/ stretch-proposed-updates main contrib non-free" >> /etc/apt/sources.list \
 && echo "deb http://archive.debian.org/debian-security stretch/updates main contrib non-free"  >> /etc/apt/sources.list

ENV TERM xterm-256color

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libncurses5 tar ca-certificates \
    && cd /usr/bin && \
    curl -sS https://www.adom.de/home/download/current/adom_linux_debian_64_3.3.3.tar.gz | tar -xz --strip-components 1 adom/adom \
    && rm -rf /var/cache/apt/* /var/lib/apt/lists/*

COPY --chmod=755 adom.py /usr/bin/adom.py

RUN useradd -m adom -d /adom
VOLUME /adom/.adom.data
USER adom
WORKDIR /adom

CMD [ "/usr/bin/adom.py" ]
