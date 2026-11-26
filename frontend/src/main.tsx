import React from 'react';
import ReactDOM from 'react-dom/client';
import { NeedleThemeProvider } from '@neo4j-ndl/react';
import '@neo4j-ndl/base/lib/neo4j-ds-styles.css';
import { App } from './App';
import './style.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><NeedleThemeProvider theme="dark" wrapperProps={{isWrappingChildren:true,style:{display:'contents'}}}><App /></NeedleThemeProvider></React.StrictMode>,
);
