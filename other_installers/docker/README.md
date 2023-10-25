Docker Quick Start (Ubuntu 20.04 LTS)
------------

:warning:
This Docker is not maintained at the moment.
If you are interested to contribute, please submit a Pull Request


1. Install Docker
```bash
sudo su
apt-get install -y curl
curl https://get.docker.com | /bin/bash
```

2. Type these commands to build the Docker image:
```bash
git clone https://github.com/ail-project/ail-framework.git
cd ail-framework
cp -r ./other_installers/docker/Dockerfile ./other_installers/docker/docker_start.sh ./other_installers/docker/pystemon ./
cp ./configs/update.cfg.sample ./configs/update.cfg
vim/nano ./configs/update.cfg (set auto_update to False)
docker build --build-arg tz_buildtime=YOUR_GEO_AREA/YOUR_CITY -t ail-framework .
```

## Single container

3. To start AIL on port 7000, type the following command below:
```
docker run -p 7000:7000 --name ail-framework ail-framework
```

4. To debug the running container, type the following command:
```bash
docker exec -it ail-framework bash
cd /opt/ail
```

## Docker-compose

3. Type these commands to start a the containers:
```bash
cd ./other_installers/docker
docker-compose up -d
```

4. To see the logs:
```bash
docker-compose logs
```

5. To stop the docker container:
```bash
docker-compose down
```
