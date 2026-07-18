import axios, { type AxiosInstance } from "axios";

/*
 * 게이트웨이(nginx) 경유 시 프론트와 API 는 동일 오리진이므로 baseURL 은 /api.
 * access 토큰 주입 / 401 시 refresh 재시도 인터셉터는 이후 인증 스테이지에서 추가한다.
 */
export const apiClient: AxiosInstance = axios.create({
  baseURL: "/api",
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

export default apiClient;
