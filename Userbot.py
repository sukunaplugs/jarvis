mkdir ai-phone-assistant
cd ai-phone-assistant
npm init -y
npm init -y
npm install express ws dotenv twilio



require('dotenv').config();
const express = require('express');
const http = require('http');
const { WebSocketServer, WebSocket } = require('ws');
const fs = require('fs');
const { exec } = require('child_process');

const app = express();
app.use(express.urlencoded({ extended: true }));

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

// 1. Twilio Webhook: When someone calls your Twilio number, Twilio hits this endpoint
app.post('/incoming-call', (req, res) => {
    const twimlResponse = `
        <Response>
            <Say>Hello! Connecting you to your AI coding assistant. Speak your commands.</Say>
            <Connect>
                <Stream url="wss://${req.headers.host}/media-stream" />
            </Connect>
        </Response>
    `;
    res.type('text/xml');
    res.send(twimlResponse);
});

// 2. WebSocket Connection for Audio Streams & VS Code Bridge
wss.on('connection', (ws, req) => {
    const url = new URL(req.url, `http://${req.headers.host}`);

    if (url.pathname === '/media-stream') {
        console.log('Incoming phone call audio stream connected.');

        ws.on('message', (message) => {
            const msg = JSON.parse(message);

            switch (msg.event) {
                case 'start':
                    console.log(`Call started. Stream SID: ${msg.streamSid}`);
                    break;
                case 'media':
                    // Here is where raw audio chunks (mulaw) from the caller arrive.
                    // You can pipe msg.payload (base64 audio) into your speech-to-speech AI model API.
                    break;
                case 'stop':
                    console.log('Call ended.');
                    break;
            }
        });
    } else if (url.pathname === '/vscode-bridge') {
        console.log('VS Code workspace connected.');

        ws.on('message', (message) => {
            const data = JSON.parse(message);

            // Execute commands received from the AI bot
            if (data.action === 'writeFile') {
                fs.writeFileSync(data.path, data.content);
                ws.send(JSON.stringify({ status: 'success', message: `File ${data.path} written successfully.` }));
            } else if (data.action === 'runTerminal') {
                exec(data.command, (err, stdout, stderr) => {
                    if (err) {
                        ws.send(JSON.stringify({ status: 'error', output: stderr }));
                    } else {
                        ws.send(JSON.stringify({ status: 'success', output: stdout }));
                    }
                });
            }
        });
    }
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Server is running on port ${PORT}`);
});



/