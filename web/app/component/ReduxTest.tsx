"use client";

import { useEffect } from "react";
import { useAppDispatch, useAppSelector } from "@/lib/redux/hooks";
import { increment, decrement, reset, incrementByAmount } from "@/lib/redux/features/counter/counterSlice";

export default function ReduxTest() {
  const dispatch = useAppDispatch();
  const { value, lastAction } = useAppSelector((state) => state.counter);

  useEffect(() => {
    console.log("[Redux State]", { counter: value, lastAction });
  }, [value, lastAction]);

  return (
    <div className="redux-test">
      <p className="redux-test-label">Redux State Test</p>
      <div className="redux-test-state">
        <span>Counter: <strong>{value}</strong></span>
        <span>Last Action: <strong>{lastAction}</strong></span>
      </div>
      <div className="redux-test-buttons">
        <button onClick={() => dispatch(decrement())}>−</button>
        <button onClick={() => dispatch(reset())}>Reset</button>
        <button onClick={() => dispatch(increment())}>+</button>
        <button onClick={() => dispatch(incrementByAmount(5))}>+5</button>
      </div>
    </div>
  );
}
