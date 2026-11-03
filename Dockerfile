# Build once, ship each page in its own image so the internal page can never
# end up on the public server by accident.
#
#   docker build --target public   -t homepage:public .
#   docker build --target internal -t homepage:internal .

FROM python:3.13-alpine AS build
WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python build.py

FROM nginx:1.27-alpine AS public
COPY --from=build /src/dist/index.html /usr/share/nginx/html/index.html

FROM nginx:1.27-alpine AS internal
COPY --from=build /src/dist/internal/index.html /usr/share/nginx/html/index.html
