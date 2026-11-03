# typed: strict
# frozen_string_literal: true

# Homebrew formula for the codu session and data browser.
class Codu < Formula
  desc "Browse local Codex sessions and data in an ncdu-style TUI"
  homepage "https://github.com/ActivationEnergy/codu"
  url "https://raw.githubusercontent.com/ActivationEnergy/codu/v0.3.2/codu.py"
  version "0.3.2"
  sha256 "6699eb173d51827985d8e5d38f8ae1863fd37f1fe28309e1ff7054fd90a1628a"
  license "MIT"

  depends_on "python@3.14"

  def install
    inreplace "codu.py", "#!/usr/bin/env python3", "#!#{formula_opt_bin("python@3.14")}/python3.14"
    bin.install "codu.py" => "codu"
  end

  test do
    assert_match "codu 0.3.2", shell_output("#{bin}/codu --version")
    assert_match "usage: codu", shell_output("#{bin}/codu --help")
  end
end
