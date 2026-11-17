FROM node:22-alpine

# Todo el contenedor corre como `node` (uid 1000) — auditoría OWASP, C5.
WORKDIR /app
RUN chown node:node /app
USER node

# Deps de website y backoffice (por separado), exactamente las del lockfile.
COPY --chown=node:node website/my-app/package.json website/my-app/package-lock.json ./uis/website/
RUN npm ci --prefix ./uis/website

COPY --chown=node:node backoffice/package.json backoffice/package-lock.json ./uis/backoffice/
RUN npm ci --prefix ./uis/backoffice

COPY --chown=node:node start.sh ./start.sh
# En Windows git puede dejar start.sh con finales CRLF y el shell del
# contenedor no lo encuentra ("./start.sh: not found"): se normaliza a LF.
RUN sed -i 's/\r$//' ./start.sh && chmod +x ./start.sh

ENV NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000 3001

CMD ["./start.sh"]
