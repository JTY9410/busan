.PHONY: docker-build docker-push docker-release hook-install

IMAGE ?= wecarmobility/busan-insurance
TAG ?= latest
SHA := sha-$(shell git rev-parse --short HEAD 2>/dev/null)

docker-build:
	docker build -t $(IMAGE):$(TAG) -t $(IMAGE):$(SHA) .

echo:
	@echo Image: $(IMAGE)
	@echo Tags: $(TAG) $(SHA)

docker-push:
	docker push $(IMAGE):$(TAG)
	docker push $(IMAGE):$(SHA)

docker-release: docker-build docker-push

hook-install:
	bash scripts/install-git-hook.sh
