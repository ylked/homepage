FROM python:3.13-alpine AS build
WORKDIR /src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python build.py

FROM nginx:1.27-alpine
COPY --from=build /src/dist/index.html /usr/share/nginx/html/index.html
