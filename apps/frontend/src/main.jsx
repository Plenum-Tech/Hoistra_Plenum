import React from 'react';
import { createRoot } from 'react-dom/client';
import './styles/base.css';
import App from './App.jsx';
import { enableReadQueue } from './api/client.js';
// Reads wait their turn for the browser's few connections, timed from when they are sent
// (api/client.js send queue).
enableReadQueue(true);
createRoot(document.getElementById('root')).render(<App />);
