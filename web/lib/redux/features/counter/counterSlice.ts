import { createSlice, PayloadAction } from "@reduxjs/toolkit";

interface CounterState {
  value: number;
  lastAction: string;
}

const initialState: CounterState = {
  value: 0,
  lastAction: "none",
};

export const counterSlice = createSlice({
  name: "counter",
  initialState,
  reducers: {
    increment: (state) => {
      state.value += 1;
      state.lastAction = "increment";
    },
    decrement: (state) => {
      state.value -= 1;
      state.lastAction = "decrement";
    },
    reset: (state) => {
      state.value = 0;
      state.lastAction = "reset";
    },
    incrementByAmount: (state, action: PayloadAction<number>) => {
      state.value += action.payload;
      state.lastAction = `incrementByAmount(${action.payload})`;
    },
  },
});

export const { increment, decrement, reset, incrementByAmount } =
  counterSlice.actions;

export default counterSlice.reducer;
