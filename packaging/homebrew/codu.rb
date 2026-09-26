# typed: strict
# frozen_string_literal: true

# Homebrew formula for the codu command-line session browser.
class Codu < Formula
  desc "Browse and manage local Codex sessions in an ncdu-style TUI"
  homepage "https://github.com/ActivationEnergy/codu"
  url "https://raw.githubusercontent.com/ActivationEnergy/codu/v0.2.0/codu.py"
  version "0.2.0"
  sha256 "36101765503101f88888f702b20ca4607eb1da6757797c046b4724c539ea5fbe"
  license "MIT"

  depends_on "python@3.14"

  def install
    inreplace "codu.py", "#!/usr/bin/env python3", "#!#{formula_opt_bin("python@3.14")}/python3.14"
    bin.install "codu.py" => "codu"
  end

  test do
    assert_match "codu 0.2.0", shell_output("#{bin}/codu --version")
    assert_match "usage: codu", shell_output("#{bin}/codu --help")
  end
end
