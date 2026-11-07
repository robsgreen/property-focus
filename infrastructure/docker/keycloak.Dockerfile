# syntax=docker/dockerfile:1
#
# Dogfood Keycloak: an optimized build of the image pinned in the root compose.yaml (keep the
# two digests equal), for Postgres with health checks and no experimental features, with
# the realm file baked in for `start --optimized --import-realm`. Keycloak imports the realm
# only when it does not exist yet; later deploys reconcile it with
# backend/scripts/keycloak_realm.py. The realm file holds placeholders, never credentials.
#
# Build from the repository root:
#   docker build -f infrastructure/docker/keycloak.Dockerfile .

FROM quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332 AS build
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332
COPY --from=build /opt/keycloak/ /opt/keycloak/
COPY infrastructure/keycloak/realm-property-focus.json /opt/keycloak/data/import/
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
CMD ["start", "--optimized", "--import-realm"]
