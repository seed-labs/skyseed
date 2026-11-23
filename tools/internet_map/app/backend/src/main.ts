import express, { Request, Response } from 'express';
import path from 'path';
import expressWs from 'express-ws';

const app = express();
expressWs(app);

import apiV1Router from './api/v1/main';

const frontendPath = path.resolve(__dirname, '../../frontend/dist/frontend');

app.use('/static', express.static(path.join(frontendPath)));

require('../skyseed').install(app);
app.use('/api/v1', apiV1Router);

app.get('/pro/*', (req: Request, res: Response) => {
  res.sendFile(path.join(frontendPath, 'index.html'));
});

app.get('/', (req: Request, res: Response) => {
  res.redirect('/pro/home');
});

app.listen(Number(process.env.SKYSEED_PORT || 8080), '127.0.0.1');