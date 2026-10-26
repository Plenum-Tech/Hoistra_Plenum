// The Trace Lab page (/lab/): its own sign-in, shell and Home, with the Trace Lab screens inside. Hoistra's design
// tokens (styles/base.css) give it Hoistra's look; nothing here is part of the Hoistra app.
import React from 'react';
import { createRoot } from 'react-dom/client';
import '../styles/base.css';
import '../tracelab/styles.css';
import './lab.css';
import LabApp from './LabApp.jsx';

createRoot(document.getElementById('root')).render(<LabApp />);
