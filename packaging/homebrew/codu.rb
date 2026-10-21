# typed: strict
# frozen_string_literal: true

# Homebrew formula for the codu session and data browser.
class Codu < Formula
  desc "Browse local Codex sessions and data in an ncdu-style TUI"
  homepage "https://github.com/ActivationEnergy/codu"
  url "https://raw.githubusercontent.com/ActivationEnergy/codu/v0.3.0/codu.py"
  version "0.3.0"
  sha256 "7407f63067e04776c8e9ffb53e8a3f4ed8d11d4fbe6a5421e92a3b152adc14f0"
  license "MIT"

  depends_on "python@3.14"

  def install
    inreplace "codu.py", "#!/usr/bin/env python3", "#!#{formula_opt_bin("python@3.14")}/python3.14"
    bin.install "codu.py" => "codu"
  end

  test do
    assert_match "codu 0.3.0", shell_output("#{bin}/codu --version")
    assert_match "usage: codu", shell_output("#{bin}/codu --help")
  end
end
