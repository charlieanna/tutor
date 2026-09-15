package main

var x int

func main() {
	done := make(chan struct{})
	go func() {
		for i := 0; i < 100000; i++ {
			x++
		}
		close(done)
	}()
	for i := 0; i < 100000; i++ {
		x++
	}
	<-done
}
