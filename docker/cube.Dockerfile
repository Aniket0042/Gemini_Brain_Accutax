# Cube for Accutax AI, with the semantic model and security config baked in.
# Build from the repository root:  docker build -f docker/cube.Dockerfile -t accutax-ai-cube .
#
# Same Cube release as semantic/docker-compose.cube.yml. The database settings and
# CUBEJS_API_SECRET come from the server's cube.env at run time, never the image.

FROM cubejs/cube:v1.7.50

COPY semantic/cube.js /cube/conf/cube.js
COPY semantic/model /cube/conf/model
