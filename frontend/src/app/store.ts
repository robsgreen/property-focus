/** The Redux store: RTK Query's cache and middleware, with the session every request uses. */
import { configureStore } from "@reduxjs/toolkit";

import type { Session } from "../auth/session";
import { baseApi } from "../api/baseApi";
import type { ApiContext } from "../api/baseApi";

export function createStore(session: Session) {
  const extraArgument: ApiContext = { session };
  return configureStore({
    reducer: { [baseApi.reducerPath]: baseApi.reducer },
    middleware: (getDefaultMiddleware) =>
      getDefaultMiddleware({ thunk: { extraArgument } }).concat(baseApi.middleware),
  });
}

export type AppStore = ReturnType<typeof createStore>;
export type RootState = ReturnType<AppStore["getState"]>;
export type AppDispatch = AppStore["dispatch"];
