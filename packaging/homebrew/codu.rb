# typed: strict
# frozen_string_literal: true

# Homebrew formula for the codu session and data browser.
class Codu < Formula
  desc "Browse local Codex sessions and data in an ncdu-style TUI"
  homepage "https://github.com/ActivationEnergy/codu"
  url "https://raw.githubusercontent.com/ActivationEnergy/codu/v0.3.3/codu.py"
  version "0.3.3"
  sha256 "ba0de7c15386e2308c8e411e5dd2949218a1a65655f207530d45b136b6117840"
  license "MIT"

  depends_on "python@3.14"

  def install
    inreplace "codu.py", "#!/usr/bin/env python3", "#!#{formula_opt_bin("python@3.14")}/python3.14"
    bin.install "codu.py" => "codu"
  end

  test do
    assert_match "codu 0.3.3", shell_output("#{bin}/codu --version")
    assert_match "usage: codu", shell_output("#{bin}/codu --help")
  end
end
