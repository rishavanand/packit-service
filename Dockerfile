FROM registry.fedoraproject.org/f29/httpd:2.4

ENV LANG=en_US.UTF-8
# nicer output from the playbook run
ENV ANSIBLE_STDOUT_CALLBACK=debug
# Ansible doesn't like /tmp
COPY files/ /src/files/
# We need to install packages. In httpd:2.4 container is user set to 1001
USER 0

RUN mkdir /home/packit
COPY files/passwd /home/packit/passwd
ENV LD_PRELOAD=libnss_wrapper.so
ENV NSS_WRAPPER_PASSWD=/home/packit/passwd
ENV NSS_WRAPPER_GROUP=/etc/group

# Install packages first and reuse the cache as much as possible
RUN dnf install -y ansible \
    && cd /src/ \
    && ansible-playbook -vv -c local -i localhost, files/install-rpm-packages.yaml \
    && dnf clean all

COPY setup.py setup.cfg files/recipe.yaml .git_archival.txt .gitattributes /src/
# setuptools-scm
COPY .git /src/.git
COPY packit_service/ /src/packit_service/

RUN cd /src/ \
    && ansible-playbook -vv -c local -i localhost, files/recipe.yaml

# TODO: add this logic to files/recipe.yaml
RUN /usr/libexec/httpd-prepare && rpm-file-permissions \
    && chmod -R a+rwx /var/lib/httpd \
    && chmod -R a+rwx /var/log/httpd

USER 1001
ENV USER=packit
ENV HOME=/home/packit

CMD ["/usr/bin/run-httpd"]
