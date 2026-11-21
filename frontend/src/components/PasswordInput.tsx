import { useState, type InputHTMLAttributes } from "react";

import { EyeIcon, EyeOffIcon } from "./icons";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

/**
 * 눈 아이콘으로 보기/숨기기를 토글하는 비밀번호 입력. 가려진 채로는 왜 틀렸는지(오타, 한 칸만
 * 자동 채움 등) 알 길이 없어 모든 비밀번호 칸에 쓴다. 나머지 props 는 `<input>` 에 그대로 넘긴다.
 */
export function PasswordInput({ className = "input", ...rest }: Props) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <input {...rest} type={visible ? "text" : "password"} className={`${className} pr-10`} />
      <button
        type="button"
        className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-muted hover:text-[color:var(--text-primary)]"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? "비밀번호 숨기기" : "비밀번호 보기"}
        aria-pressed={visible}
        title={visible ? "비밀번호 숨기기" : "비밀번호 보기"}
      >
        {visible ? <EyeOffIcon /> : <EyeIcon />}
      </button>
    </div>
  );
}
