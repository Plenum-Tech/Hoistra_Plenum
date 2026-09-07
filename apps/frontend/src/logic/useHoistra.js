import { useRef, useReducer, useEffect } from 'react';
import { HoistraLogic } from './HoistraLogic.js';

// One controller per app; components receive its renderVals() as `vals`.
export function useHoistra() {
  const ref = useRef(null);
  if (!ref.current) ref.current = new HoistraLogic();
  const [, tick] = useReducer((n) => n + 1, 0);
  useEffect(() => {
    const c = ref.current;
    const un = c.subscribe(tick);
    if (c.componentDidMount) c.componentDidMount();
    return () => { un(); if (c.componentWillUnmount) c.componentWillUnmount(); };
  }, []);
  return ref.current.renderVals();
}
